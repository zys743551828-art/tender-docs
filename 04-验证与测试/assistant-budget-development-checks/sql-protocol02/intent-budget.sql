-- Fresh isolated schema only, after assistant.sql and the reviewed fact/identity capability.
-- No LOGIN creation, historical backfill, external fee authorization or runtime repair.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_catalog.pg_roles WHERE rolname='tender_assistant_budget_admin') THEN
  CREATE ROLE tender_assistant_budget_admin NOLOGIN;
 END IF;
END $$;
CREATE TABLE design.assistant_price_versions (
 id uuid PRIMARY KEY,version text NOT NULL UNIQUE,
 provider text NOT NULL CHECK(provider='deepseek'),model text NOT NULL CHECK(model IN ('deepseek-flash','deepseek-v4-pro')),
 currency text NOT NULL CHECK(currency IN ('USD','CNY')),
 amount_scale bigint NOT NULL DEFAULT 1000000000 CHECK(amount_scale=1000000000),
 input_per_million_minor bigint NOT NULL CHECK(input_per_million_minor BETWEEN 1 AND 1000000000000),
 output_per_million_minor bigint NOT NULL CHECK(output_per_million_minor BETWEEN 1 AND 1000000000000),
 input_hard_ceiling integer NOT NULL CHECK(input_hard_ceiling BETWEEN 1 AND 2097152),
 output_hard_ceiling integer NOT NULL CHECK(output_hard_ceiling BETWEEN 1 AND 8192),
 source_kind text NOT NULL CHECK(source_kind IN ('official_tariff_and_context_limit','explicit_mock_fixture')),
 source_uri text NOT NULL CHECK(source_uri LIKE 'https://%'),source_sha256 design.sha256 NOT NULL,
 verified_at timestamptz NOT NULL,valid_from timestamptz NOT NULL,valid_until timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(verified_at<=created_at AND valid_from<valid_until AND valid_until>created_at),
 CHECK(source_kind<>'official_tariff_and_context_limit' OR source_uri ~ '^https://(api-docs[.]deepseek[.]com|platform[.]deepseek[.]com)(/|$)')
);
CREATE TABLE design.assistant_budget_versions (
 id uuid PRIMARY KEY,scope text NOT NULL CHECK(scope IN ('global','member','project')),
 owner_key text NOT NULL,currency text NOT NULL CHECK(currency IN ('USD','CNY')),
 revision bigint NOT NULL CHECK(revision>0),limit_minor bigint NOT NULL CHECK(limit_minor BETWEEN 0 AND 9000000000000000000),
 window_from timestamptz NOT NULL,window_until timestamptz NOT NULL,
 configured_reason text NOT NULL CHECK(length(btrim(configured_reason))>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(scope,owner_key,revision),CHECK(window_from<window_until),
 CHECK((scope='global' AND owner_key='assistant') OR (scope='member' AND owner_key ~ '^[0-9a-f-]{36}$') OR (scope='project' AND owner_key ~ '^[0-9a-f]{64}$'))
);
CREATE TABLE design.assistant_logical_intents (
 id uuid PRIMARY KEY,job_id uuid NOT NULL UNIQUE REFERENCES design.assistant_jobs(id),
 actor_id uuid NOT NULL REFERENCES design.members(id),
 request_mode text NOT NULL CHECK(request_mode='new_question'),
 normalized_question text NOT NULL CHECK(length(btrim(normalized_question)) BETWEEN 1 AND 400000),
 purpose text NOT NULL CHECK(purpose IN ('general_reference','project_reference','finding_explanation','rule_explanation','section_reference','section_draft')),
 target_key text NOT NULL,coarse_key design.sha256 NOT NULL,anti_resend_key design.sha256 NOT NULL,
 canonical_project_key design.sha256,target jsonb NOT NULL CHECK(jsonb_typeof(target)='object'),
 explicit_send boolean NOT NULL,
 origin_action_id uuid UNIQUE REFERENCES design.proposed_actions(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(id,job_id)
);
CREATE TABLE design.assistant_budget_reservations (
 job_id uuid PRIMARY KEY REFERENCES design.assistant_logical_intents(job_id),
 intent_id uuid NOT NULL UNIQUE REFERENCES design.assistant_logical_intents(id),
 actor_id uuid NOT NULL REFERENCES design.members(id),coarse_key design.sha256 NOT NULL,
 price_id uuid NOT NULL REFERENCES design.assistant_price_versions(id),
 member_budget_id uuid NOT NULL REFERENCES design.assistant_budget_versions(id),
 project_budget_id uuid REFERENCES design.assistant_budget_versions(id),
 global_budget_id uuid NOT NULL REFERENCES design.assistant_budget_versions(id),
 currency text NOT NULL CHECK(currency IN ('USD','CNY')),
 reserved_minor bigint NOT NULL CHECK(reserved_minor>0),input_upper integer NOT NULL CHECK(input_upper>0),
 output_upper integer NOT NULL CHECK(output_upper BETWEEN 1 AND 8192),
 state text NOT NULL DEFAULT 'held' CHECK(state IN ('held','sent','unknown','settled','released')),
 requires_manual_reconciliation boolean NOT NULL DEFAULT false,
 charged_minor bigint CHECK(charged_minor>=0),
 project_call_id uuid UNIQUE REFERENCES design.model_calls(id),
 global_call_id uuid UNIQUE REFERENCES design.assistant_global_calls(id),
 dispatched_at timestamptz,settled_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(NOT(project_call_id IS NOT NULL AND global_call_id IS NOT NULL)),
 CHECK((state IN ('settled','released'))=(charged_minor IS NOT NULL AND settled_at IS NOT NULL)),
 CHECK(state<>'released' OR charged_minor=0),
 CHECK(state NOT IN ('sent','unknown') OR dispatched_at IS NOT NULL),
 CHECK(dispatched_at IS NULL OR project_call_id IS NOT NULL OR global_call_id IS NOT NULL)
);
-- Admission is serialized by the global RPC lock. A late reconciliation conflict may
-- legitimately reopen an older hold beside a later one; never discard its actual usage.
CREATE INDEX assistant_open_object_purpose ON design.assistant_budget_reservations(coarse_key) WHERE state IN ('held','sent','unknown');
CREATE TABLE design.assistant_budget_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 job_id uuid NOT NULL REFERENCES design.assistant_budget_reservations(job_id),
 event_type text NOT NULL CHECK(event_type IN ('reserved','dispatched','usage_observed','usage','unknown','not_sent','reconciled','reconciliation_conflict')),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION design.assistant_normalize_question(value text) RETURNS text LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT btrim(regexp_replace(normalize(replace(replace(value,chr(13)||chr(10),chr(10)),chr(13),chr(10)),NFKC),'[[:space:]]+',' ','g'));
$$;
CREATE FUNCTION design.assistant_check_target(target jsonb,actor uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,design AS $$
DECLARE proof jsonb;fact jsonb;record_fact record;bound record;kind text;bid uuid;canonical text;assembled text='';name text;object_id uuid;stable text;object_row record;
BEGIN
 kind=target->>'kind';
 IF kind='general' THEN
  IF target->>'target_key' IS DISTINCT FROM 'general:'||actor::text OR target->'id' IS DISTINCT FROM 'null'::jsonb OR target->'proof' IS DISTINCT FROM 'null'::jsonb OR target->>'purpose' IS DISTINCT FROM 'general_reference' THEN RAISE EXCEPTION 'ASSISTANT_TARGET_INVALID' USING ERRCODE='23514'; END IF;
  RETURN;
 END IF;
 IF kind NOT IN ('project','finding','rule','section') THEN RAISE EXCEPTION 'ASSISTANT_TARGET_INVALID' USING ERRCODE='23514'; END IF;
 proof=target->'proof';bid=(target->>'bid_id')::uuid;object_id=(target->>'id')::uuid;
 SELECT a.id,a.payload,p.round_id,p.bidder_id,r.engineering_project_id INTO bound
 FROM design.bid_profiles p JOIN design.bid_rounds r ON r.id=p.round_id
 JOIN LATERAL(SELECT id,payload FROM design.audit_entries WHERE bid_id=p.bid_id AND event_type='identity.bound' ORDER BY coalesce((payload->>'identity_epoch')::bigint,0) DESC,created_at DESC,id DESC LIMIT 1)a ON true WHERE p.bid_id=bid;
 IF bound.id IS NULL OR bound.id::text IS DISTINCT FROM proof->>'identity_authority_id'
  OR (bound.payload->>'confirmed')::boolean IS DISTINCT FROM true
  OR bound.payload->'after'->>'round_id' IS DISTINCT FROM bound.round_id::text
  OR bound.payload->'after'->>'bidder_id' IS DISTINCT FROM bound.bidder_id::text
  OR bound.payload->'after'->>'engineering_project_id' IS DISTINCT FROM bound.engineering_project_id::text
  OR NOT EXISTS(SELECT FROM design.fact_heads h WHERE h.bid_id=bid AND h.fact_set_id=(proof->>'fact_set_id')::uuid)
  OR jsonb_array_length(proof->'identity_facts') IS DISTINCT FROM 5 THEN RAISE EXCEPTION 'ASSISTANT_TARGET_IDENTITY_UNPROVEN' USING ERRCODE='23514'; END IF;
 FOREACH name IN ARRAY ARRAY['procurement_issuer_identifier','procurement_project_identifier','procurement_lot_identifier','procurement_round_identifier','bidder_identity_identifier'] LOOP
  SELECT value INTO fact FROM jsonb_array_elements(proof->'identity_facts') WHERE value->>'name'=name;
  SELECT f.*,d.id AS decision_id,d.decision,d.evidence_ids AS decision_evidence INTO record_fact FROM design.fact_items f
  JOIN design.decision_heads h ON h.bid_id=f.bid_id AND h.object_kind='fact' AND h.object_id=f.id
  JOIN design.decisions d ON d.id=h.decision_id
  WHERE f.id=(fact->>'id')::uuid AND f.bid_id=bid AND f.fact_set_id=(proof->>'fact_set_id')::uuid AND f.name=name;
  IF record_fact.id IS NULL OR record_fact.decision<>'confirmed' OR record_fact.decision_id::text IS DISTINCT FROM fact->>'decision_id'
   OR record_fact.value IS DISTINCT FROM fact->'value' OR jsonb_typeof(record_fact.value)<>'string'
   OR NOT(record_fact.evidence_ids<@record_fact.decision_evidence AND record_fact.evidence_ids@>record_fact.decision_evidence) OR cardinality(record_fact.evidence_ids)=0
   THEN RAISE EXCEPTION 'ASSISTANT_TARGET_IDENTITY_UNPROVEN' USING ERRCODE='23514'; END IF;
  IF assembled<>'' THEN assembled=assembled||chr(31); END IF;
  assembled=assembled||name||'='||design.assistant_normalize_question(record_fact.value#>>'{}');
 END LOOP;
 canonical=encode(sha256(convert_to(assembled,'UTF8')),'hex');
 IF canonical IS DISTINCT FROM target->>'canonical_project_key' OR canonical IS DISTINCT FROM proof->>'canonical_project_key' THEN RAISE EXCEPTION 'ASSISTANT_TARGET_INVALID' USING ERRCODE='23514'; END IF;
 IF (kind='project' AND object_id<>bid)
  OR (kind='finding' AND NOT EXISTS(SELECT FROM design.findings WHERE id=object_id AND bid_id=bid))
  OR (kind='rule' AND NOT EXISTS(SELECT FROM design.rule_items WHERE id=object_id AND bid_id=bid))
  OR (kind='section' AND NOT EXISTS(SELECT FROM design.sections WHERE id=object_id AND bid_id=bid AND head_revision=(target->'object_context'->>'head_revision')::bigint))
  THEN RAISE EXCEPTION 'ASSISTANT_TARGET_INVALID' USING ERRCODE='23514'; END IF;
 IF kind='project' THEN stable='project';
 ELSIF kind='section' THEN stable=object_id::text;
 ELSIF kind='rule' THEN
  SELECT * INTO object_row FROM design.rule_items WHERE id=object_id;
  stable=coalesce(object_row.manual_origin_rule_id::text,encode(sha256(convert_to(design.assistant_normalize_question(object_row.kind)||chr(31)||design.assistant_normalize_question(object_row.statement),'UTF8')),'hex'));
 ELSE
  WITH RECURSIVE chain AS(SELECT id,carried_from_id,ARRAY[id] AS path FROM design.findings WHERE id=object_id AND bid_id=bid
   UNION ALL SELECT f.id,f.carried_from_id,c.path||f.id FROM design.findings f JOIN chain c ON f.id=c.carried_from_id WHERE f.bid_id=bid AND NOT f.id=ANY(c.path) AND cardinality(c.path)<100)
  SELECT id::text INTO stable FROM chain WHERE carried_from_id IS NULL;
 END IF;
 IF stable IS NULL OR target->>'target_key' IS DISTINCT FROM canonical||':'||kind||':'||stable
  OR NOT((kind='project' AND target->>'purpose'='project_reference') OR(kind='finding' AND target->>'purpose'='finding_explanation') OR(kind='rule' AND target->>'purpose'='rule_explanation') OR(kind='section' AND target->>'purpose' IN ('section_reference','section_draft')))
 THEN RAISE EXCEPTION 'ASSISTANT_TARGET_INVALID' USING ERRCODE='23514'; END IF;
END $$;
CREATE FUNCTION design.assistant_budget_obligation(policy uuid) RETURNS numeric LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,design AS $$
 SELECT coalesce(sum(CASE WHEN r.state IN ('held','sent','unknown') THEN r.reserved_minor ELSE r.charged_minor END),0)
 FROM design.assistant_budget_versions current_policy
 JOIN design.assistant_budget_versions original_policy ON original_policy.scope=current_policy.scope AND original_policy.owner_key=current_policy.owner_key
 JOIN design.assistant_budget_reservations r ON original_policy.id IN(r.member_budget_id,r.project_budget_id,r.global_budget_id)
 WHERE current_policy.id=policy AND original_policy.currency=current_policy.currency AND (r.state IN ('held','sent','unknown') OR r.created_at>=current_policy.window_from AND r.created_at<current_policy.window_until);
$$;
CREATE FUNCTION design.assistant_reserve_budget(job uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,design AS $$
DECLARE j record;i record;p record;policy record;m uuid;project uuid;g uuid;amount bigint;output_tokens integer;scope_name text;owner text;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'ASSISTANT_REQUIRES_READ_COMMITTED' USING ERRCODE='25000'; END IF;
 PERFORM pg_advisory_xact_lock(90129,1);
 SELECT * INTO j FROM design.assistant_jobs WHERE id=job;
 SELECT * INTO i FROM design.assistant_logical_intents WHERE job_id=job;
 IF j.id IS NULL OR i.id IS NULL OR i.actor_id<>j.actor_id OR i.target IS DISTINCT FROM j.input_manifest->'logical_intent'->'target'
  OR i.target_key IS DISTINCT FROM i.target->>'target_key' OR i.purpose IS DISTINCT FROM i.target->>'purpose'
  OR i.canonical_project_key IS DISTINCT FROM i.target->>'canonical_project_key'
  OR i.origin_action_id IS DISTINCT FROM j.origin_action_id
  OR i.explicit_send IS DISTINCT FROM (j.input_manifest->'logical_intent'->>'explicit_send')::boolean
  OR i.id::text IS DISTINCT FROM j.input_manifest->'logical_intent'->>'id'
  OR i.anti_resend_key IS DISTINCT FROM j.intent_digest
  OR (i.purpose='section_draft') IS DISTINCT FROM (j.kind='authoring')
  OR i.normalized_question IS DISTINCT FROM design.assistant_normalize_question(coalesce(j.input_manifest->'request'->>'text',j.input_manifest->'request'->>'instruction'))
  OR i.coarse_key<>encode(sha256(convert_to(i.target_key||chr(31)||i.purpose,'UTF8')),'hex')
  OR i.anti_resend_key<>encode(sha256(convert_to(i.target_key||chr(31)||i.purpose||chr(31)||i.normalized_question,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ASSISTANT_INTENT_INVALID' USING ERRCODE='23514'; END IF;
 PERFORM design.assistant_check_target(i.target,i.actor_id);
 IF EXISTS(SELECT FROM design.assistant_budget_reservations WHERE job_id=job) THEN RETURN (SELECT to_jsonb(r) FROM design.assistant_budget_reservations r WHERE r.job_id=job); END IF;
 IF EXISTS(SELECT FROM design.assistant_jobs old LEFT JOIN design.assistant_budget_reservations r ON r.job_id=old.id
  WHERE r.job_id IS NULL AND (EXISTS(SELECT FROM design.assistant_global_calls c WHERE c.run_id=old.id AND c.state IN ('reserved','unknown'))
  OR EXISTS(SELECT FROM design.model_calls c WHERE c.operation_id=old.operation_id AND c.state IN ('reserved','unknown'))))
  THEN RAISE EXCEPTION 'ASSISTANT_UNPRICED_OBLIGATION' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT FROM design.assistant_budget_reservations r JOIN design.assistant_jobs old ON old.id=r.job_id WHERE r.coarse_key=i.coarse_key AND (r.state IN ('held','sent','unknown')
   OR EXISTS(SELECT FROM design.operations o WHERE o.id=old.operation_id AND o.state IN ('queued','running','cancel_requested'))
   OR EXISTS(SELECT FROM design.assistant_global_runs rn WHERE rn.id=old.id AND rn.state IN ('queued','running','cancel_requested'))))
  THEN RAISE EXCEPTION 'ASSISTANT_INDEPENDENCE_UNPROVEN' USING ERRCODE='23514'; END IF;
 IF i.target->>'kind'='section' AND EXISTS(
  SELECT FROM design.assistant_logical_intents old JOIN design.assistant_budget_reservations r ON r.job_id=old.job_id
  WHERE r.state IN ('held','sent','unknown') AND old.purpose=i.purpose AND old.canonical_project_key=i.canonical_project_key AND old.target->>'kind'='section'
  AND EXISTS(SELECT FROM design.section_revisions left_revision JOIN design.section_revisions right_revision ON right_revision.digest=left_revision.digest WHERE left_revision.section_id=(old.target->>'id')::uuid AND right_revision.section_id=(i.target->>'id')::uuid))
  THEN RAISE EXCEPTION 'ASSISTANT_INDEPENDENCE_UNPROVEN' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT FROM design.assistant_budget_reservations r JOIN design.assistant_jobs old ON old.id=r.job_id WHERE r.actor_id=i.actor_id AND (r.state IN ('sent','unknown')
  OR EXISTS(SELECT FROM design.assistant_global_calls call WHERE call.run_id=old.id AND call.state IN ('reserved','unknown'))
  OR EXISTS(SELECT FROM design.model_calls call WHERE call.operation_id=old.operation_id AND call.state IN ('reserved','unknown')))) AND NOT i.explicit_send
  THEN RAISE EXCEPTION 'ASSISTANT_EXPLICIT_NEW_QUESTION_REQUIRED' USING ERRCODE='23514'; END IF;
 SELECT * INTO p FROM design.assistant_price_versions WHERE id=(j.input_manifest->'logical_intent'->>'price_version_id')::uuid AND valid_from<=clock_timestamp() AND valid_until>clock_timestamp();
 output_tokens=coalesce((j.input_manifest->'request'->>'max_tokens')::integer,2048);
 IF p.id IS NULL OR output_tokens>p.output_hard_ceiling THEN RAISE EXCEPTION 'ASSISTANT_PRICE_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 amount=ceil((p.input_hard_ceiling::numeric*p.input_per_million_minor+output_tokens::numeric*p.output_per_million_minor)/1000000)::bigint;
 FOREACH scope_name IN ARRAY ARRAY['global','member','project'] LOOP
  IF scope_name='project' AND i.canonical_project_key IS NULL THEN CONTINUE; END IF;
  owner=CASE scope_name WHEN 'global' THEN 'assistant' WHEN 'member' THEN i.actor_id::text ELSE i.canonical_project_key END;
  SELECT * INTO policy FROM design.assistant_budget_versions b WHERE b.scope=scope_name AND b.owner_key=owner ORDER BY revision DESC LIMIT 1;
  IF policy.id IS NULL OR policy.currency<>p.currency OR policy.window_from>clock_timestamp() OR policy.window_until<=clock_timestamp() THEN RAISE EXCEPTION 'ASSISTANT_BUDGET_NOT_CONFIGURED' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT FROM design.assistant_budget_versions old JOIN design.assistant_budget_reservations r ON old.id IN(r.member_budget_id,r.project_budget_id,r.global_budget_id) WHERE old.scope=scope_name AND old.owner_key=owner AND (r.state IN ('held','sent','unknown') OR r.created_at>=policy.window_from AND r.created_at<policy.window_until) AND r.currency<>p.currency) THEN RAISE EXCEPTION 'ASSISTANT_BUDGET_CURRENCY_UNRESOLVED' USING ERRCODE='23514'; END IF;
  IF design.assistant_budget_obligation(policy.id)+amount>policy.limit_minor THEN RAISE EXCEPTION 'ASSISTANT_BUDGET_EXHAUSTED' USING ERRCODE='23514'; END IF;
  IF scope_name='global' THEN g=policy.id; ELSIF scope_name='member' THEN m=policy.id; ELSE project=policy.id; END IF;
 END LOOP;
 INSERT INTO design.assistant_budget_reservations(job_id,intent_id,actor_id,coarse_key,price_id,member_budget_id,project_budget_id,global_budget_id,currency,reserved_minor,input_upper,output_upper)
 VALUES(job,i.id,i.actor_id,i.coarse_key,p.id,m,project,g,p.currency,amount,p.input_hard_ceiling,output_tokens);
 INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,'reserved',jsonb_build_object('worst_case_minor',amount,'currency',p.currency,'price_version_id',p.id,'estimate_not_provider_spend_cap',true));
 RETURN (SELECT to_jsonb(r) FROM design.assistant_budget_reservations r WHERE r.job_id=job);
END $$;
CREATE FUNCTION design.assistant_dispatch_budget(job uuid,call_id uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,design AS $$
DECLARE j record;i record;r record;p record;native record;policy record;scope_name text;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'ASSISTANT_REQUIRES_READ_COMMITTED' USING ERRCODE='25000'; END IF;
 PERFORM pg_advisory_xact_lock(90129,1);
 SELECT * INTO j FROM design.assistant_jobs WHERE id=job;
 SELECT * INTO i FROM design.assistant_logical_intents WHERE job_id=job;
 SELECT * INTO r FROM design.assistant_budget_reservations WHERE job_id=job FOR UPDATE;
 IF r.job_id IS NULL OR r.state<>'held' OR r.dispatched_at IS NOT NULL THEN RAISE EXCEPTION 'ASSISTANT_CALL_REQUIRES_RECONCILIATION' USING ERRCODE='23514'; END IF;
 PERFORM design.assistant_check_target(i.target,i.actor_id);
 SELECT * INTO p FROM design.assistant_price_versions WHERE id=r.price_id;
 IF p.valid_until<=clock_timestamp() THEN RAISE EXCEPTION 'ASSISTANT_PRICE_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 FOR policy IN SELECT * FROM design.assistant_budget_versions WHERE id IN(r.member_budget_id,r.project_budget_id,r.global_budget_id) LOOP
  IF policy.window_from>clock_timestamp() OR policy.window_until<=clock_timestamp()
   OR EXISTS(SELECT FROM design.assistant_budget_versions newer WHERE newer.scope=policy.scope AND newer.owner_key=policy.owner_key AND newer.revision>policy.revision)
   OR design.assistant_budget_obligation(policy.id)>policy.limit_minor THEN RAISE EXCEPTION 'ASSISTANT_BUDGET_CONFIGURATION_CHANGED' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF i.origin_action_id IS NOT NULL AND NOT EXISTS(SELECT FROM design.action_consumptions a WHERE a.action_id=i.origin_action_id AND a.result->'assistant_resolution'->>'state'='executed' AND a.result->'assistant_resolution'->'domain_result'->>'id'=job::text) THEN RAISE EXCEPTION 'ASSISTANT_EFFECT_IDENTITY_INVALID' USING ERRCODE='23514'; END IF;
 IF j.scope='global' THEN
  SELECT c.state,rn.state AS execution_state,rn.cancel_requested_at,rn.lease_until INTO native FROM design.assistant_global_calls c JOIN design.assistant_global_runs rn ON rn.id=c.run_id WHERE c.id=call_id AND c.run_id=job;
 ELSE
  SELECT c.state,o.state AS execution_state,o.cancel_requested_at,s.lease_until INTO native FROM design.model_calls c JOIN design.operations o ON o.id=c.operation_id JOIN design.step_receipts s ON s.operation_id=o.id AND s.state='running' WHERE c.id=call_id AND c.operation_id=j.operation_id;
 END IF;
 IF native.state IS DISTINCT FROM 'reserved' OR native.execution_state IS DISTINCT FROM 'running' OR native.cancel_requested_at IS NOT NULL OR native.lease_until IS NULL OR native.lease_until<=clock_timestamp() THEN RAISE EXCEPTION 'MODEL_EXECUTION_STALE' USING ERRCODE='23514'; END IF;
 UPDATE design.assistant_budget_reservations SET state='sent',dispatched_at=clock_timestamp(),global_call_id=CASE WHEN j.scope='global' THEN call_id END,project_call_id=CASE WHEN j.scope='project' THEN call_id END WHERE job_id=job;
 INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,'dispatched',jsonb_build_object('call_id',call_id,'scope',j.scope));
 RETURN (SELECT to_jsonb(row) FROM design.assistant_budget_reservations row WHERE row.job_id=job);
END $$;
CREATE FUNCTION design.assistant_settle_budget(job uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,design AS $$
DECLARE r record;p record;j record;native record;amount bigint;input_tokens bigint;output_tokens bigint;billing text;state_name text;
BEGIN
 PERFORM pg_advisory_xact_lock(90129,1);
 SELECT * INTO r FROM design.assistant_budget_reservations WHERE job_id=job FOR UPDATE;
 IF r.job_id IS NULL THEN RAISE EXCEPTION 'ASSISTANT_BUDGET_NOT_CONFIGURED' USING ERRCODE='23514'; END IF;
 IF r.requires_manual_reconciliation THEN RETURN to_jsonb(r); END IF;
 SELECT * INTO j FROM design.assistant_jobs WHERE id=job;
 SELECT * INTO p FROM design.assistant_price_versions WHERE id=r.price_id;
 IF r.global_call_id IS NOT NULL THEN SELECT state,usage INTO native FROM design.assistant_global_calls WHERE id=r.global_call_id;
 ELSIF r.project_call_id IS NOT NULL THEN SELECT state,usage INTO native FROM design.model_calls WHERE id=r.project_call_id;
 ELSE
  -- A failed pre-send gate can leave a native reservation, but no dispatch authority.
  IF r.dispatched_at IS NULL AND ((j.scope='global' AND EXISTS(SELECT FROM design.assistant_global_calls WHERE run_id=job AND state='failed' AND usage->>'billing_state'='not_sent'))
   OR (j.scope='project' AND EXISTS(SELECT FROM design.model_calls WHERE operation_id=j.operation_id AND state='failed' AND usage->>'billing_state'='not_sent'))) THEN
   UPDATE design.assistant_budget_reservations SET state='released',charged_minor=0,settled_at=clock_timestamp() WHERE job_id=job AND state='held';
   IF FOUND THEN INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,'not_sent',jsonb_build_object('basis','native_failed_before_dispatch')); END IF;
   RETURN (SELECT to_jsonb(row) FROM design.assistant_budget_reservations row WHERE row.job_id=job);
  END IF;
  IF (j.scope='global' AND EXISTS(SELECT FROM design.assistant_global_runs WHERE id=job AND state IN ('cancelled','failed')) AND NOT EXISTS(SELECT FROM design.assistant_global_calls WHERE run_id=job))
   OR (j.scope='project' AND EXISTS(SELECT FROM design.operations WHERE id=j.operation_id AND state IN ('cancelled','failed')) AND NOT EXISTS(SELECT FROM design.model_calls WHERE operation_id=j.operation_id)) THEN
   IF r.state='held' THEN
    UPDATE design.assistant_budget_reservations SET state='released',charged_minor=0,settled_at=clock_timestamp() WHERE job_id=job;
    INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,'not_sent',jsonb_build_object('basis','worker_terminal_without_native_call'));
   END IF;
  END IF;
  RETURN (SELECT to_jsonb(row) FROM design.assistant_budget_reservations row WHERE row.job_id=job);
 END IF;
 billing=native.usage->>'billing_state';
 IF billing='unknown' OR (native.state IN ('reserved','unknown') AND billing NOT IN ('usage_reported','not_sent','trusted_reconciled')) THEN
  IF r.state NOT IN ('settled','released','unknown') THEN
   UPDATE design.assistant_budget_reservations SET state='unknown' WHERE job_id=job;
   INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,'unknown',jsonb_build_object('native_state',native.state,'usage',native.usage,'hold_unchanged',true));
  END IF;
  RETURN (SELECT to_jsonb(row) FROM design.assistant_budget_reservations row WHERE row.job_id=job);
 END IF;
 IF billing='not_sent' THEN amount=0;state_name='released';
 ELSIF billing='usage_reported' THEN
  IF jsonb_typeof(native.usage->'actual'->'prompt_tokens') IS DISTINCT FROM 'number' OR jsonb_typeof(native.usage->'actual'->'completion_tokens') IS DISTINCT FROM 'number'
   OR (native.usage->'actual'->>'prompt_tokens' ~ '^[0-9]+$') IS DISTINCT FROM true OR (native.usage->'actual'->>'completion_tokens' ~ '^[0-9]+$') IS DISTINCT FROM true THEN RAISE EXCEPTION 'ASSISTANT_USAGE_OUTSIDE_PRICE_BOUND' USING ERRCODE='23514'; END IF;
  input_tokens=(native.usage->'actual'->>'prompt_tokens')::bigint;output_tokens=(native.usage->'actual'->>'completion_tokens')::bigint;
  IF input_tokens<0 OR output_tokens<0 OR input_tokens>r.input_upper OR output_tokens>r.output_upper THEN RAISE EXCEPTION 'ASSISTANT_USAGE_OUTSIDE_PRICE_BOUND' USING ERRCODE='23514'; END IF;
  amount=ceil((input_tokens::numeric*p.input_per_million_minor+output_tokens::numeric*p.output_per_million_minor)/1000000)::bigint;state_name='settled';
 ELSE RETURN (SELECT to_jsonb(row) FROM design.assistant_budget_reservations row WHERE row.job_id=job);
 END IF;
 IF r.state IN ('settled','released') THEN
  IF r.charged_minor<>amount THEN
   INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,'reconciliation_conflict',jsonb_build_object('prior',r.charged_minor,'new_usage_charge',amount,'native_usage',native.usage));
   -- A conflict restores the original worst-case obligation; no automatic fee release.
   UPDATE design.assistant_budget_reservations SET state='unknown',charged_minor=NULL,settled_at=NULL,requires_manual_reconciliation=true WHERE job_id=job;
  END IF;
 ELSE
  UPDATE design.assistant_budget_reservations SET state=state_name,charged_minor=amount,settled_at=clock_timestamp() WHERE job_id=job;
  INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,CASE WHEN state_name='released' THEN 'not_sent' ELSE 'usage' END,jsonb_build_object('native_usage',native.usage,'computed_ceiling_charge_minor',amount,'price_version_id',p.id,'provider_invoice_amount',NULL));
 END IF;
 RETURN (SELECT to_jsonb(row) FROM design.assistant_budget_reservations row WHERE row.job_id=job);
END $$;
CREATE FUNCTION design.assistant_observe_usage(job uuid,call_id uuid,reported jsonb,request_id text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,design AS $$
DECLARE r record;native_state text;value jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(90129,1);
 SELECT * INTO r FROM design.assistant_budget_reservations WHERE job_id=job FOR UPDATE;
 IF r.job_id IS NULL OR r.dispatched_at IS NULL OR (r.global_call_id IS DISTINCT FROM call_id AND r.project_call_id IS DISTINCT FROM call_id)
  OR jsonb_typeof(reported) IS DISTINCT FROM 'object' OR request_id IS NULL OR length(request_id) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'ASSISTANT_USAGE_IDENTITY_INVALID' USING ERRCODE='23514'; END IF;
 IF r.global_call_id IS NOT NULL THEN
  SELECT state INTO native_state FROM design.assistant_global_calls WHERE id=call_id;
  UPDATE design.assistant_global_calls SET usage=coalesce(usage,'{}'::jsonb)||jsonb_build_object('billing_state','usage_reported','actual',reported,'late_provider_request_id',request_id) WHERE id=call_id;
 ELSE
  SELECT state INTO native_state FROM design.model_calls WHERE id=call_id;
  UPDATE design.model_calls SET usage=coalesce(usage,'{}'::jsonb)||jsonb_build_object('billing_state','usage_reported','actual',reported,'late_provider_request_id',request_id) WHERE id=call_id;
 END IF;
 INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,'usage_observed',jsonb_build_object('native_call_id',call_id,'native_state_at_observation',native_state,'reported_usage',reported,'provider_request_id',request_id,'native_execution_not_reopened',true));
 BEGIN
  value=design.assistant_settle_budget(job);
 EXCEPTION WHEN check_violation OR numeric_value_out_of_range THEN
  UPDATE design.assistant_budget_reservations SET state='unknown',charged_minor=NULL,settled_at=NULL,requires_manual_reconciliation=true WHERE job_id=job;
  INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,'reconciliation_conflict',jsonb_build_object('reason','usage_outside_frozen_bound','reported_usage',reported,'original_worst_case_hold_retained',true));
  SELECT to_jsonb(row) INTO value FROM design.assistant_budget_reservations row WHERE row.job_id=job;
 END;
 RETURN value;
END $$;
CREATE FUNCTION design.assistant_reconcile_budget(job uuid,decision text,amount bigint,proof_reference text,proof_sha design.sha256) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,design AS $$
DECLARE r record;j record;native record;
BEGIN
 PERFORM pg_advisory_xact_lock(90129,1);
 SELECT * INTO r FROM design.assistant_budget_reservations WHERE job_id=job FOR UPDATE;
 SELECT * INTO j FROM design.assistant_jobs WHERE id=job;
 IF r.job_id IS NULL OR decision NOT IN ('confirmed_not_sent','confirmed_billed','confirmed_zero_charge_failure') OR length(btrim(proof_reference))<10 OR amount<0 OR amount>r.reserved_minor OR (decision<>'confirmed_billed' AND amount<>0) THEN RAISE EXCEPTION 'ASSISTANT_RECONCILIATION_INVALID' USING ERRCODE='23514'; END IF;
 IF r.state NOT IN ('unknown','sent') THEN RAISE EXCEPTION 'ASSISTANT_RECONCILIATION_INVALID' USING ERRCODE='23514'; END IF;
 IF j.scope='global' THEN
  SELECT * INTO native FROM design.assistant_global_runs WHERE id=job;
  IF native.state NOT IN ('failed','cancelled') THEN RAISE EXCEPTION 'ASSISTANT_EXECUTION_NOT_TERMINAL' USING ERRCODE='23514'; END IF;
  UPDATE design.assistant_global_calls SET state='failed',usage=coalesce(usage,'{}'::jsonb)||jsonb_build_object('billing_state','trusted_reconciled','reconciliation',decision) WHERE id=r.global_call_id;
 ELSE
  SELECT * INTO native FROM design.operations WHERE id=j.operation_id;
  IF native.state NOT IN ('failed','cancelled') THEN RAISE EXCEPTION 'ASSISTANT_EXECUTION_NOT_TERMINAL' USING ERRCODE='23514'; END IF;
  UPDATE design.model_calls SET state='failed',usage=coalesce(usage,'{}'::jsonb)||jsonb_build_object('billing_state','trusted_reconciled','reconciliation',decision) WHERE id=r.project_call_id;
 END IF;
 INSERT INTO design.assistant_budget_events(job_id,event_type,payload) VALUES(job,'reconciled',jsonb_build_object('decision',decision,'billed_minor',amount,'currency',r.currency,'proof_reference',proof_reference,'proof_sha256',proof_sha,'previous_reservation',to_jsonb(r),'original_execution_unchanged',true));
 UPDATE design.assistant_budget_reservations SET state=CASE WHEN decision='confirmed_not_sent' THEN 'released' ELSE 'settled' END,charged_minor=amount,settled_at=clock_timestamp(),requires_manual_reconciliation=false WHERE job_id=job;
 RETURN (SELECT to_jsonb(row) FROM design.assistant_budget_reservations row WHERE row.job_id=job);
END $$;
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['assistant_price_versions','assistant_budget_versions','assistant_logical_intents','assistant_budget_events'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',name);
 END LOOP;
END $$;
REVOKE ALL ON design.assistant_price_versions,design.assistant_budget_versions,design.assistant_logical_intents,design.assistant_budget_reservations,design.assistant_budget_events FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_score_read,tender_research_read,tender_assistant_budget_admin;
GRANT USAGE ON SCHEMA design TO tender_assistant_budget_admin;
GRANT SELECT,INSERT ON design.assistant_price_versions,design.assistant_budget_versions TO tender_assistant_budget_admin;
GRANT SELECT ON design.assistant_logical_intents,design.assistant_budget_reservations,design.assistant_budget_events TO tender_assistant_budget_admin;
GRANT SELECT ON design.assistant_price_versions,design.assistant_budget_versions,design.assistant_logical_intents,design.assistant_budget_reservations,design.assistant_budget_events TO tender_api,tender_worker;
GRANT INSERT ON design.assistant_logical_intents TO tender_api;
REVOKE ALL ON FUNCTION design.assistant_normalize_question(text),design.assistant_check_target(jsonb,uuid),design.assistant_budget_obligation(uuid),design.assistant_reserve_budget(uuid),design.assistant_dispatch_budget(uuid,uuid),design.assistant_settle_budget(uuid),design.assistant_observe_usage(uuid,uuid,jsonb,text),design.assistant_reconcile_budget(uuid,text,bigint,text,design.sha256) FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_score_read,tender_research_read,tender_assistant_budget_admin;
GRANT EXECUTE ON FUNCTION design.assistant_normalize_question(text),design.assistant_budget_obligation(uuid),design.assistant_reserve_budget(uuid) TO tender_api;
GRANT EXECUTE ON FUNCTION design.assistant_dispatch_budget(uuid,uuid),design.assistant_settle_budget(uuid),design.assistant_observe_usage(uuid,uuid,jsonb,text) TO tender_worker;
GRANT EXECUTE ON FUNCTION design.assistant_settle_budget(uuid) TO tender_api;
GRANT EXECUTE ON FUNCTION design.assistant_reconcile_budget(uuid,text,bigint,text,design.sha256) TO tender_assistant_budget_admin;
COMMIT;
