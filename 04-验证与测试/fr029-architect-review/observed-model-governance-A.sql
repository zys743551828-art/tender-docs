-- FR029 independent fresh-initialization DRAFT A. Do not execute or hotpatch existing DBs.
-- Requires baseline and research-source-qualification.sql. No existing table/role grants change.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='tender_model_train') THEN CREATE ROLE tender_model_train NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='tender_model_evaluate') THEN CREATE ROLE tender_model_evaluate NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='tender_model_score') THEN CREATE ROLE tender_model_score NOLOGIN; END IF;
END $$;
CREATE SCHEMA model_governance;
REVOKE ALL ON SCHEMA model_governance FROM PUBLIC;
GRANT USAGE ON SCHEMA model_governance TO tender_api,tender_model_train,tender_model_evaluate,tender_model_score;
CREATE TABLE model_governance.protocols(
 id uuid PRIMARY KEY, digest design.sha256 NOT NULL UNIQUE, document jsonb NOT NULL,
 origin text NOT NULL CHECK(origin IN ('synthetic','real')), created_by uuid NOT NULL REFERENCES design.members(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=1048576)
);
CREATE TABLE model_governance.cohorts(
 id uuid PRIMARY KEY, research_run_id uuid NOT NULL REFERENCES design.research_runs(id),
 protocol_id uuid NOT NULL REFERENCES model_governance.protocols(id), digest design.sha256 NOT NULL,
 origin text NOT NULL CHECK(origin IN ('synthetic','real')), test_start timestamptz NOT NULL,
 created_by uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE model_governance.members(
 cohort_id uuid NOT NULL REFERENCES model_governance.cohorts(id),sample_id uuid NOT NULL REFERENCES design.samples(id),
 purpose text NOT NULL CHECK(purpose IN ('fit','development','calibration','final','excluded')),
 bid_id uuid NOT NULL REFERENCES design.bids(id),submission_id uuid NOT NULL REFERENCES design.submission_records(id),
 outcome_id uuid NOT NULL REFERENCES design.outcomes(id),group_id uuid NOT NULL,project_id uuid NOT NULL,
 body_digest design.sha256 NOT NULL,template_key text,near_duplicate_key text,
 grouping_decision_id uuid NOT NULL REFERENCES design.decisions(id),verification_decision_id uuid NOT NULL REFERENCES design.decisions(id),
 identity_authority_id uuid NOT NULL REFERENCES design.audit_entries(id),identity_snapshot jsonb NOT NULL,
 submitted_at timestamptz NOT NULL,known_at timestamptz NOT NULL,
 PRIMARY KEY(cohort_id,sample_id)
);
CREATE TABLE model_governance.source_bindings(
 cohort_id uuid NOT NULL,sample_id uuid NOT NULL,source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),
 ancestor_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),revision bigint NOT NULL,
 digest design.sha256 NOT NULL,policy_class text NOT NULL CHECK(policy_class IN ('prebid_content','official_label')),known_at timestamptz NOT NULL,valid_from timestamptz NOT NULL,valid_until timestamptz,
 PRIMARY KEY(cohort_id,sample_id,source_id,ancestor_id),
 FOREIGN KEY(cohort_id,sample_id) REFERENCES model_governance.members(cohort_id,sample_id)
);
-- Small, purpose-bound JSON exports: no shared bucket credential is required by CPU roles.
CREATE TABLE model_governance.exports(
 id uuid PRIMARY KEY,cohort_id uuid NOT NULL REFERENCES model_governance.cohorts(id),
 purpose text NOT NULL CHECK(purpose IN ('training','final_features','final_labels')),
 digest design.sha256 NOT NULL,payload jsonb NOT NULL,
 CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=8388608),UNIQUE(cohort_id,purpose)
);
CREATE TABLE model_governance.jobs(
 id uuid PRIMARY KEY,cohort_id uuid NOT NULL REFERENCES model_governance.cohorts(id),
 kind text NOT NULL CHECK(kind IN ('train','evaluate')),input_digest design.sha256 NOT NULL,
 generation bigint NOT NULL DEFAULT 1 CHECK(generation>0),state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
 created_by uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE model_governance.leases(
 job_id uuid PRIMARY KEY REFERENCES model_governance.jobs(id),owner_token uuid NOT NULL,fence bigint NOT NULL CHECK(fence>0),
 generation bigint NOT NULL,lease_until timestamptz NOT NULL
);
CREATE TABLE model_governance.results(
 job_id uuid PRIMARY KEY REFERENCES model_governance.jobs(id),digest design.sha256 NOT NULL,payload jsonb NOT NULL,
 owner_token uuid NOT NULL,fence bigint NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=8388608)
);
-- API freezes the selected JSON model and final predictions before any label opening.
CREATE TABLE model_governance.evaluations(
 job_id uuid PRIMARY KEY REFERENCES model_governance.jobs(id),training_job_id uuid NOT NULL REFERENCES model_governance.results(job_id),
 model_digest design.sha256 NOT NULL,predictions_digest design.sha256 NOT NULL,predictions jsonb NOT NULL,
 protocol_digest design.sha256 NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(jsonb_typeof(predictions)='object' AND octet_length(predictions::text)<=8388608)
);
CREATE TABLE model_governance.exposures(
 id uuid PRIMARY KEY,job_id uuid NOT NULL UNIQUE REFERENCES model_governance.evaluations(job_id),
 model_digest design.sha256 NOT NULL,predictions_digest design.sha256 NOT NULL,protocol_digest design.sha256 NOT NULL,
 created_xact xid8 NOT NULL DEFAULT pg_current_xact_id(),opened_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- Global closure identity uniqueness, not merely a manifest or experiment identifier.
CREATE TABLE model_governance.exposure_members(
 kind text NOT NULL CHECK(kind IN ('sample','group','project','body','template','near_duplicate')),
 identity text NOT NULL,exposure_id uuid NOT NULL REFERENCES model_governance.exposures(id),PRIMARY KEY(kind,identity)
);
CREATE TABLE model_governance.model_records(
 model_id uuid PRIMARY KEY REFERENCES design.model_artifacts(id),training_job_id uuid NOT NULL REFERENCES model_governance.results(job_id),
 origin text NOT NULL CHECK(origin IN ('synthetic','real')),model_digest design.sha256 NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE model_governance.route_events(
 id uuid PRIMARY KEY,route_key text NOT NULL,revision bigint NOT NULL CHECK(revision>0),
 model_id uuid REFERENCES design.model_artifacts(id),acceptance_id uuid REFERENCES design.model_acceptances(id),
 action text NOT NULL CHECK(action IN ('publish','rollback','suspend')),actor_id uuid NOT NULL REFERENCES design.members(id),
 reason text NOT NULL CHECK(length(btrim(reason))>0),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(route_key,revision)
);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['protocols','cohorts','members','source_bindings','exports','results','evaluations','exposures','exposure_members','model_records','route_events'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON model_governance.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',tab);
 END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA model_governance FROM PUBLIC,tender_worker,tender_research_worker,tender_research_read,tender_score_read,tender_model_train,tender_model_evaluate,tender_model_score;
GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA model_governance TO tender_api;
GRANT UPDATE(state,generation) ON model_governance.jobs TO tender_api;

-- Caller must enter with 91143, then ALL cohort bids sorted, then graph, before final effects.
-- Function takes the same locks itself so a narrow role cannot omit the lock scope.
CREATE FUNCTION model_governance.current_cohort(target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,retrieval,pg_temp AS $$
DECLARE member record;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RETURN false; END IF;
 PERFORM pg_advisory_xact_lock(91143,1);
 FOR member IN SELECT DISTINCT bid_id FROM model_governance.members WHERE cohort_id=target ORDER BY bid_id LOOP
  PERFORM design.lock_worker_bid(member.bid_id);
 END LOOP;
 PERFORM pg_advisory_xact_lock(90127,1);
 IF NOT EXISTS(SELECT FROM model_governance.members WHERE cohort_id=target) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.members frozen
  LEFT JOIN design.samples sample ON sample.id=frozen.sample_id
  LEFT JOIN design.submission_records submission ON submission.id=frozen.submission_id
  LEFT JOIN design.submission_heads sh ON sh.bid_id=frozen.bid_id
  LEFT JOIN design.outcome_heads oh ON oh.submission_id=frozen.submission_id
  LEFT JOIN design.decision_heads grouping ON grouping.bid_id=frozen.bid_id AND grouping.object_kind='sample_group' AND grouping.object_id=frozen.sample_id
  LEFT JOIN design.decision_heads verification ON verification.bid_id=frozen.bid_id AND verification.object_kind='outcome' AND verification.object_id=frozen.outcome_id
  LEFT JOIN design.bid_profiles profile ON profile.bid_id=frozen.bid_id
  WHERE frozen.cohort_id=target AND (sample.admission IS DISTINCT FROM 'admitted'
   OR sample.source_group_id IS DISTINCT FROM frozen.group_id OR sample.outcome_id IS DISTINCT FROM frozen.outcome_id
   OR sh.submission_id IS DISTINCT FROM frozen.submission_id OR oh.outcome_id IS DISTINCT FROM frozen.outcome_id
   OR submission.evidence->'identity' IS DISTINCT FROM frozen.identity_snapshot
   OR grouping.decision_id IS DISTINCT FROM frozen.grouping_decision_id
   OR verification.decision_id IS DISTINCT FROM frozen.verification_decision_id
   OR profile.bidder_id IS DISTINCT FROM (frozen.identity_snapshot->>'bidder_id')::uuid
   OR profile.round_id IS DISTINCT FROM (frozen.identity_snapshot->>'round_id')::uuid
   OR frozen.identity_authority_id IS DISTINCT FROM (SELECT a.id FROM design.audit_entries a WHERE a.bid_id=frozen.bid_id AND a.event_type='identity.bound' ORDER BY coalesce((a.payload->>'identity_epoch')::bigint,0) DESC,a.created_at DESC,a.id DESC LIMIT 1)
  )) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.source_bindings frozen
  LEFT JOIN retrieval.source_nodes n ON n.id=frozen.ancestor_id LEFT JOIN retrieval.source_heads h ON h.source_id=n.id
  WHERE frozen.cohort_id=target AND (n.digest IS DISTINCT FROM frozen.digest OR n.policy_class IS DISTINCT FROM frozen.policy_class
   OR h.status IS DISTINCT FROM 'published' OR h.revision IS DISTINCT FROM frozen.revision
   OR n.known_at IS DISTINCT FROM frozen.known_at OR h.valid_from IS DISTINCT FROM frozen.valid_from OR h.valid_until IS DISTINCT FROM frozen.valid_until
   OR h.valid_from>clock_timestamp() OR h.valid_until<=clock_timestamp()
   OR NOT EXISTS(SELECT FROM retrieval.source_ancestors a WHERE a.source_id=frozen.source_id AND a.ancestor_id=frozen.ancestor_id))) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.source_bindings selected JOIN retrieval.source_ancestors a ON a.source_id=selected.source_id
  WHERE selected.cohort_id=target AND NOT EXISTS(SELECT FROM model_governance.source_bindings frozen WHERE frozen.cohort_id=target AND frozen.sample_id=selected.sample_id AND frozen.source_id=a.source_id AND frozen.ancestor_id=a.ancestor_id)) THEN RETURN false; END IF;
 RETURN NOT EXISTS(SELECT FROM model_governance.members m WHERE m.cohort_id=target AND NOT EXISTS(SELECT FROM model_governance.source_bindings s WHERE s.cohort_id=target AND s.sample_id=m.sample_id));
END $$;
REVOKE ALL ON FUNCTION model_governance.current_cohort(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_governance.current_cohort(uuid) TO tender_api;

CREATE FUNCTION model_governance.claim_job(target uuid,token uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,pg_temp AS $$
DECLARE job model_governance.jobs; previous model_governance.leases; intended text; next_fence bigint;
BEGIN
 SELECT * INTO job FROM model_governance.jobs WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'job_missing' USING ERRCODE='P0002'; END IF;
 intended:=CASE job.kind WHEN 'train' THEN 'tender_model_train' ELSE 'tender_model_evaluate' END;
 IF NOT pg_has_role(session_user,intended,'member') THEN RAISE EXCEPTION 'purpose_denied' USING ERRCODE='42501'; END IF;
 IF NOT model_governance.current_cohort(job.cohort_id) THEN RAISE EXCEPTION 'cohort_stale' USING ERRCODE='55000'; END IF;
 SELECT * INTO job FROM model_governance.jobs WHERE id=target FOR UPDATE;
 IF job.state NOT IN ('queued','running') THEN RETURN NULL; END IF;
 SELECT * INTO previous FROM model_governance.leases WHERE job_id=target FOR UPDATE;
 IF FOUND AND previous.lease_until>clock_timestamp() THEN RETURN NULL; END IF;
 next_fence:=coalesce(previous.fence,0)+1;
 INSERT INTO model_governance.leases VALUES(target,token,next_fence,job.generation,clock_timestamp()+interval '120 seconds')
 ON CONFLICT(job_id) DO UPDATE SET owner_token=excluded.owner_token,fence=excluded.fence,generation=excluded.generation,lease_until=excluded.lease_until;
 UPDATE model_governance.jobs SET state='running' WHERE id=target;
 RETURN jsonb_build_object('job_id',target,'kind',job.kind,'fence',next_fence,'generation',job.generation,'input_digest',job.input_digest);
END $$;
CREATE FUNCTION model_governance.assert_lease(target uuid,token uuid,expected_fence bigint) RETURNS model_governance.jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,pg_temp AS $$
DECLARE job model_governance.jobs; intended text;
BEGIN
 SELECT * INTO job FROM model_governance.jobs WHERE id=target;
 intended:=CASE job.kind WHEN 'train' THEN 'tender_model_train' ELSE 'tender_model_evaluate' END;
 IF job.id IS NULL OR NOT pg_has_role(session_user,intended,'member') THEN RAISE EXCEPTION 'purpose_denied' USING ERRCODE='42501'; END IF;
 IF NOT model_governance.current_cohort(job.cohort_id) THEN RAISE EXCEPTION 'cohort_stale' USING ERRCODE='55000'; END IF;
 PERFORM 1 FROM model_governance.jobs WHERE id=target AND state='running' FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS(SELECT FROM model_governance.leases l WHERE l.job_id=target AND l.owner_token=token AND l.fence=expected_fence AND l.generation=job.generation AND l.lease_until>clock_timestamp()) THEN RAISE EXCEPTION 'lease_invalid' USING ERRCODE='55000'; END IF;
 RETURN job;
END $$;
CREATE FUNCTION model_governance.read_training(target uuid,token uuid,expected_fence bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,pg_temp AS $$
DECLARE job model_governance.jobs;
BEGIN
 job:=model_governance.assert_lease(target,token,expected_fence);
 IF job.kind<>'train' THEN RAISE EXCEPTION 'purpose_denied' USING ERRCODE='42501'; END IF;
 RETURN (SELECT jsonb_build_object('protocol',p.document,'origin',c.origin,'export',e.payload,'export_digest',e.digest)
 FROM model_governance.cohorts c JOIN model_governance.protocols p ON p.id=c.protocol_id
 JOIN model_governance.exports e ON e.cohort_id=c.id AND e.purpose='training' WHERE c.id=job.cohort_id);
END $$;
CREATE FUNCTION model_governance.open_final(target uuid,token uuid,expected_fence bigint,exposure uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,pg_temp AS $$
DECLARE job model_governance.jobs; evaluation model_governance.evaluations; previous uuid;
BEGIN
 job:=model_governance.assert_lease(target,token,expected_fence);
 IF job.kind<>'evaluate' THEN RAISE EXCEPTION 'purpose_denied' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(91144,1);
 SELECT id INTO previous FROM model_governance.exposures WHERE job_id=target;
 IF FOUND THEN RETURN previous; END IF;
 SELECT * INTO evaluation FROM model_governance.evaluations WHERE job_id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'predictions_not_frozen' USING ERRCODE='55000'; END IF;
 INSERT INTO model_governance.exposures(id,job_id,model_digest,predictions_digest,protocol_digest)
 VALUES(exposure,target,evaluation.model_digest,evaluation.predictions_digest,evaluation.protocol_digest);
 INSERT INTO model_governance.exposure_members(kind,identity,exposure_id)
 SELECT DISTINCT v.kind,v.identity,exposure FROM model_governance.members m
 CROSS JOIN LATERAL (VALUES('sample',m.sample_id::text),('group',m.group_id::text),('project',m.project_id::text),('body',m.body_digest::text),('template',m.template_key),('near_duplicate',m.near_duplicate_key)) v(kind,identity)
 WHERE m.cohort_id=job.cohort_id AND m.purpose='final' AND v.identity IS NOT NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'final_members_missing' USING ERRCODE='55000'; END IF;
 RETURN exposure;
END $$;
CREATE FUNCTION model_governance.read_final(target uuid,token uuid,expected_fence bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,pg_temp AS $$
DECLARE job model_governance.jobs;
BEGIN
 job:=model_governance.assert_lease(target,token,expected_fence);
 IF job.kind<>'evaluate' OR NOT EXISTS(SELECT FROM model_governance.exposures x WHERE x.job_id=target AND x.created_xact<>pg_current_xact_id()) THEN
  RAISE EXCEPTION 'committed_exposure_required' USING ERRCODE='55000';
 END IF;
 RETURN (SELECT jsonb_build_object('protocol',p.document,'origin',c.origin,'labels',e.payload,'labels_digest',e.digest,'predictions',v.predictions,'model_digest',v.model_digest)
 FROM model_governance.cohorts c JOIN model_governance.protocols p ON p.id=c.protocol_id
 JOIN model_governance.exports e ON e.cohort_id=c.id AND e.purpose='final_labels'
 JOIN model_governance.evaluations v ON v.job_id=target WHERE c.id=job.cohort_id);
END $$;
CREATE FUNCTION model_governance.finish_job(target uuid,token uuid,expected_fence bigint,result_digest design.sha256,result_payload jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,pg_temp AS $$
DECLARE job model_governance.jobs;
BEGIN
 job:=model_governance.assert_lease(target,token,expected_fence);
 IF encode(sha256(convert_to(result_payload::text,'UTF8')),'hex')<>result_digest THEN RAISE EXCEPTION 'result_digest_invalid' USING ERRCODE='22023'; END IF;
 INSERT INTO model_governance.results VALUES(target,result_digest,result_payload,token,expected_fence,clock_timestamp());
 UPDATE model_governance.jobs SET state='succeeded' WHERE id=target;
 RETURN true;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA model_governance FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_governance.claim_job(uuid,uuid),model_governance.finish_job(uuid,uuid,bigint,design.sha256,jsonb) TO tender_model_train,tender_model_evaluate;
GRANT EXECUTE ON FUNCTION model_governance.read_training(uuid,uuid,bigint) TO tender_model_train;
GRANT EXECUTE ON FUNCTION model_governance.open_final(uuid,uuid,bigint,uuid),model_governance.read_final(uuid,uuid,bigint) TO tender_model_evaluate;
-- Scoring has zero business reads/execute until the separate score authority+ledger draft is reviewed.
COMMIT;
