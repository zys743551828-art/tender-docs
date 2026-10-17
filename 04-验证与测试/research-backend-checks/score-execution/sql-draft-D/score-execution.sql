-- Dedicated scoring DRAFT D: static review, then NEW fresh DB only. No consumers enabled.
-- Requires research03 source authority, historical input C and model release B.
BEGIN;
CREATE SCHEMA score_execution;
REVOKE ALL ON SCHEMA score_execution FROM PUBLIC;
GRANT USAGE ON SCHEMA score_execution TO tender_api,tender_model_score;
CREATE TABLE score_execution.inputs(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES design.bids(id),
 review_id uuid,historical_input_id uuid REFERENCES historical_backtest.inputs(id),identity_snapshot jsonb,
 origin text NOT NULL CHECK(origin IN ('synthetic','uncertain')),method_digest design.sha256 NOT NULL,
 review_root_content text,review_root_digest design.sha256,
 price_config jsonb NOT NULL,technical_maximum numeric,maximum_verified boolean NOT NULL DEFAULT false CHECK(NOT maximum_verified),
 actor_id uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,review_id) REFERENCES design.review_runs(bid_id,id),
 CHECK((review_id IS NULL)<>(historical_input_id IS NULL)),
 CHECK((review_id IS NOT NULL AND jsonb_typeof(identity_snapshot)='object') OR (historical_input_id IS NOT NULL AND identity_snapshot IS NULL)),
 CHECK(technical_maximum IS NULL),
 CHECK((review_id IS NOT NULL AND review_root_content IS NOT NULL AND review_root_digest IS NOT NULL
  AND octet_length(review_root_content) BETWEEN 1 AND 8388608 AND jsonb_typeof(review_root_content::jsonb)='object'
  AND encode(sha256(convert_to(review_root_content,'UTF8')),'hex')=review_root_digest::text)
  OR (historical_input_id IS NOT NULL AND review_root_content IS NULL AND review_root_digest IS NULL)),
 CHECK(method_digest::text='276a2c953aa86d935d46aabba432f7503ab6ce8081a9375b490ecb2172fd4d48'),
 CHECK(coalesce(jsonb_typeof(price_config)='object' AND price_config ?& ARRAY['profile_digest','currency','context_tokens','max_output_tokens','input_per_million','output_per_million'] AND price_config->>'profile_digest'=method_digest::text
  AND price_config->>'currency'='USD' AND price_config->'context_tokens'='1000000'::jsonb
  AND price_config->'max_output_tokens'='4096'::jsonb,false))
);
CREATE TABLE score_execution.current_chapters(
 input_id uuid NOT NULL REFERENCES score_execution.inputs(id),chapter_package_id uuid NOT NULL REFERENCES design.chapter_packages(id),
 raw_content text NOT NULL,byte_digest design.sha256 NOT NULL,
 PRIMARY KEY(input_id,chapter_package_id),
 CHECK(octet_length(raw_content) BETWEEN 1 AND 2097152 AND jsonb_typeof(raw_content::jsonb)='object'),
 CHECK(encode(sha256(convert_to(raw_content,'UTF8')),'hex')=byte_digest::text)
);
CREATE TABLE score_execution.input_seals(
 input_id uuid PRIMARY KEY REFERENCES score_execution.inputs(id),digest design.sha256 NOT NULL,execution_key design.sha256 NOT NULL,
 rule_count integer NOT NULL CHECK(rule_count BETWEEN 1 AND 1000),created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE score_execution.jobs(
 id uuid PRIMARY KEY,input_id uuid NOT NULL REFERENCES score_execution.input_seals(input_id),execution_key design.sha256 NOT NULL,
 command_id uuid NOT NULL UNIQUE REFERENCES design.commands(id),created_by uuid NOT NULL REFERENCES design.members(id),
 state text NOT NULL CHECK(state IN ('queued','running','paused','succeeded','cancelled','failed')),
 generation bigint NOT NULL DEFAULT 1 CHECK(generation>0),fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0),owner_token uuid,lease_until timestamptz,
 budget numeric NOT NULL CHECK(budget>0 AND budget NOT IN ('NaN','Infinity','-Infinity')),
 reserved_fee numeric NOT NULL DEFAULT 0 CHECK(reserved_fee>=0),estimated_fee numeric NOT NULL DEFAULT 0 CHECK(estimated_fee>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),finished_at timestamptz,
 CHECK((owner_token IS NULL)=(lease_until IS NULL)),CHECK(estimated_fee+reserved_fee<=budget)
);
CREATE INDEX score_semantic_execution ON score_execution.jobs(execution_key);
CREATE UNIQUE INDEX active_score_input ON score_execution.jobs(input_id) WHERE state IN ('queued','running','paused');
CREATE TABLE score_execution.calls(
 id uuid PRIMARY KEY,job_id uuid NOT NULL REFERENCES score_execution.jobs(id),chapter_id uuid NOT NULL,
 replicate integer NOT NULL CHECK(replicate BETWEEN 1 AND 3),chapter_digest design.sha256 NOT NULL,request_digest design.sha256 NOT NULL,profile_digest design.sha256 NOT NULL,
 owner_token uuid NOT NULL,state text NOT NULL CHECK(state IN ('reserved','succeeded','not_sent','unknown')),
 reserved_fee numeric NOT NULL CHECK(reserved_fee>0 AND reserved_fee NOT IN ('NaN','Infinity','-Infinity')),
 estimated_fee numeric CHECK(estimated_fee>=0 AND estimated_fee NOT IN ('NaN','Infinity','-Infinity')),
 response text,response_digest design.sha256,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),settled_at timestamptz,
 UNIQUE(job_id,chapter_id,replicate),UNIQUE(profile_digest,chapter_digest,replicate),
 CHECK((state='succeeded')=(response IS NOT NULL AND response_digest IS NOT NULL)),
 CHECK(response IS NULL OR (octet_length(response)<=2097152 AND jsonb_typeof(response::jsonb)='object'
  AND encode(sha256(convert_to(response,'UTF8')),'hex')=response_digest::text))
);
-- Immutable capabilities bind a target job's exact current chapter/replicate to an
-- actual global logical call. Reused successes carry no new reservation or fee.
CREATE TABLE score_execution.call_bindings(
 job_id uuid NOT NULL REFERENCES score_execution.jobs(id),chapter_id uuid NOT NULL,
 replicate integer NOT NULL CHECK(replicate BETWEEN 1 AND 3),call_id uuid NOT NULL REFERENCES score_execution.calls(id),
 reused boolean NOT NULL,PRIMARY KEY(job_id,chapter_id,replicate),UNIQUE(job_id,call_id)
);
CREATE TABLE score_execution.results(
 job_id uuid PRIMARY KEY REFERENCES score_execution.jobs(id),input_digest design.sha256 NOT NULL,
 document jsonb NOT NULL,digest design.sha256 NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=8388608)
);
CREATE TABLE score_execution.shadows(
 id uuid PRIMARY KEY,job_id uuid NOT NULL REFERENCES score_execution.results(job_id),model_id uuid NOT NULL REFERENCES model_release.registrations(model_id),
 route_key text NOT NULL,route_revision bigint NOT NULL,document jsonb NOT NULL,digest design.sha256 NOT NULL,
 created_by uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(job_id,model_id,route_revision),CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=2097152)
);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['inputs','current_chapters','input_seals','call_bindings','results','shadows'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON score_execution.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',tab);
 END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA score_execution FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_research_read,tender_score_read,tender_model_train,tender_model_evaluate,tender_model_score;
GRANT SELECT ON ALL TABLES IN SCHEMA score_execution TO tender_api;
GRANT INSERT ON score_execution.inputs,score_execution.current_chapters TO tender_api;

CREATE FUNCTION score_execution.exact_keys(payload jsonb,allowed text[]) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF payload IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' OR allowed IS NULL THEN RETURN false; END IF;
 RETURN payload ?& allowed AND (SELECT count(*) FROM jsonb_object_keys(payload))=cardinality(allowed);
END $$;
CREATE FUNCTION score_execution.lock_input(target uuid) RETURNS score_execution.inputs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,design,pg_temp AS $$
DECLARE result score_execution.inputs;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'fresh_read_committed_required' USING ERRCODE='55000'; END IF;
 SELECT * INTO result FROM score_execution.inputs WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'score_input_missing' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(91143,1);
 PERFORM design.lock_worker_bid(result.bid_id);
 RETURN result;
END $$;

-- Current review qualification is derived from API-frozen canonical research03 capsules,
-- not from a caller's source/claim allowlist. No research run, outcome or label is read.
CREATE FUNCTION score_execution.current_chapter(target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,design,retrieval,pg_temp AS $$
DECLARE ctx record; capsule design.research_chapter_authorities; binding jsonb; head jsonb;
 source_ids uuid[]; expected_ids uuid[]; actual_ids uuid[]; selected_ids uuid[];
 node record; parent record; block_parse record; known_count integer;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RETURN false; END IF;
 SELECT cp.*,i.body_version_id,i.material_snapshot_id,i.as_of,(i.manifest->>'rule_set_id')::uuid AS rule_set_id,
  rule.evidence_ids AS rule_evidence
 INTO ctx FROM design.chapter_packages cp JOIN design.review_runs r ON r.id=cp.review_id AND r.bid_id=cp.bid_id
 JOIN design.review_inputs i ON i.id=r.input_id AND i.bid_id=cp.bid_id
 JOIN design.rule_items rule ON rule.id=cp.rule_id AND rule.bid_id=cp.bid_id AND rule.rule_set_id=(i.manifest->>'rule_set_id')::uuid
 JOIN design.bid_profiles profile ON profile.bid_id=cp.bid_id AND profile.current_review_id=cp.review_id
  AND profile.current_body_id=i.body_version_id AND profile.current_material_id=i.material_snapshot_id AND profile.current_rule_set_id=rule.rule_set_id
 WHERE cp.id=target AND rule.kind='scoring' AND rule.provenance_status='effective'
  AND cp.required_ranges='["full-body"]'::jsonb AND cp.read_ranges=cp.required_ranges AND cp.unread_ranges='[]'::jsonb;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO capsule FROM design.research_chapter_authorities WHERE chapter_package_id=target;
 IF NOT FOUND OR capsule.bid_id IS DISTINCT FROM ctx.bid_id OR capsule.as_of IS DISTINCT FROM ctx.as_of
  OR capsule.authority_digest IS DISTINCT FROM encode(sha256(convert_to(jsonb_build_object('bindings',capsule.bindings,'ancestor_heads',capsule.ancestor_heads)::text,'UTF8')),'hex') THEN RETURN false; END IF;
 SELECT array_agg(id ORDER BY id) INTO expected_ids FROM (
  SELECT e.id FROM design.evidence_blocks e WHERE e.bid_id=ctx.bid_id AND e.parse_id=ctx.parse_id UNION SELECT unnest(ctx.rule_evidence)
 ) expected;
 SELECT array_agg(DISTINCT entry::uuid ORDER BY entry::uuid),array_agg(DISTINCT (b->>'source_id')::uuid ORDER BY (b->>'source_id')::uuid)
 INTO actual_ids,source_ids FROM jsonb_array_elements(capsule.bindings) b CROSS JOIN LATERAL jsonb_array_elements_text(b->'evidence_ids') entry;
 IF expected_ids IS NULL OR actual_ids IS DISTINCT FROM expected_ids OR cardinality(source_ids)<>jsonb_array_length(capsule.bindings) THEN RETURN false; END IF;
 FOR binding IN SELECT value FROM jsonb_array_elements(capsule.bindings) LOOP
  SELECT p.*,v.artifact_kind,b.digest AS original_digest INTO block_parse FROM design.parse_runs p
   JOIN design.document_versions v ON v.id=p.document_version_id JOIN design.blobs b ON b.id=v.blob_id
   WHERE p.id=(binding->>'parse_id')::uuid AND p.bid_id=ctx.bid_id AND p.document_version_id=(binding->>'document_version_id')::uuid;
  IF NOT FOUND OR block_parse.id IS DISTINCT FROM (SELECT latest.id FROM design.parse_runs latest WHERE latest.document_version_id=block_parse.document_version_id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
   OR block_parse.coverage<>'complete' OR block_parse.gaps<>'[]'::jsonb OR block_parse.page_count<=0
   OR block_parse.page_count<>(SELECT count(*) FROM design.pages page WHERE page.parse_id=block_parse.id)
   OR EXISTS(SELECT FROM design.pages page WHERE page.parse_id=block_parse.id AND (page.physical_page NOT BETWEEN 1 AND block_parse.page_count OR page.quality<>'usable' OR page.gaps<>'[]'::jsonb))
   OR (block_parse.id=ctx.parse_id AND block_parse.document_version_id IS DISTINCT FROM ctx.body_version_id)
   OR (block_parse.id<>ctx.parse_id AND NOT EXISTS(SELECT FROM design.material_assignments m WHERE m.snapshot_id=ctx.material_snapshot_id AND m.bid_id=ctx.bid_id AND m.document_version_id=block_parse.document_version_id AND m.role IN ('tender','supplement'))) THEN RETURN false; END IF;
  SELECT array_agg(e.id ORDER BY e.id) INTO selected_ids FROM design.evidence_blocks e WHERE e.id=ANY(expected_ids) AND e.bid_id=ctx.bid_id AND e.parse_id=block_parse.id
   AND e.kind IN ('text','table') AND length(btrim(e.text_content))>0 AND encode(sha256(convert_to(e.text_content,'UTF8')),'hex')=e.digest::text
   AND EXISTS(SELECT FROM design.pages p WHERE p.bid_id=e.bid_id AND p.parse_id=e.parse_id AND p.physical_page=e.physical_page);
  SELECT array_agg(entry::uuid ORDER BY entry::uuid) INTO actual_ids FROM jsonb_array_elements_text(binding->'evidence_ids') entry;
  IF selected_ids IS NULL OR actual_ids IS DISTINCT FROM selected_ids THEN RETURN false; END IF;
  SELECT * INTO node FROM retrieval.source_nodes WHERE id=(binding->>'source_id')::uuid;
  SELECT * INTO parent FROM retrieval.source_nodes WHERE id=(binding->>'original_source_id')::uuid;
  IF node.id IS NULL OR parent.id IS NULL OR node.kind<>'document' OR parent.kind<>'document'
   OR node.document_version_id IS DISTINCT FROM block_parse.document_version_id OR parent.document_version_id IS DISTINCT FROM block_parse.document_version_id
   OR node.digest IS DISTINCT FROM block_parse.original_digest OR parent.digest IS DISTINCT FROM block_parse.original_digest
   OR NOT EXISTS(SELECT FROM retrieval.lineage_edges edge WHERE edge.child_id=node.id AND edge.parent_id=parent.id)
   OR NOT EXISTS(SELECT FROM retrieval.source_heads h WHERE h.source_id=node.id AND h.revision=(binding->>'source_revision')::bigint) THEN RETURN false; END IF;
 END LOOP;
 IF NOT EXISTS(SELECT FROM jsonb_array_elements(capsule.bindings) b WHERE (b->>'parse_id')::uuid=ctx.parse_id)
  OR EXISTS(SELECT FROM unnest(ctx.rule_evidence) rule_id WHERE NOT EXISTS(SELECT FROM design.evidence_blocks e JOIN design.parse_runs p ON p.id=e.parse_id JOIN design.material_assignments m ON m.document_version_id=p.document_version_id AND m.snapshot_id=ctx.material_snapshot_id AND m.bid_id=ctx.bid_id AND m.role IN ('tender','supplement') WHERE e.id=rule_id)) THEN RETURN false; END IF;
 SELECT count(*) INTO known_count FROM retrieval.source_ancestors a WHERE a.source_id=ANY(source_ids);
 IF known_count<>jsonb_array_length(capsule.ancestor_heads) OR EXISTS(SELECT FROM retrieval.source_ancestors a WHERE a.source_id=ANY(source_ids)
  AND NOT EXISTS(SELECT FROM jsonb_array_elements(capsule.ancestor_heads) frozen WHERE (frozen->>'source_id')::uuid=a.source_id AND (frozen->>'id')::uuid=a.ancestor_id)) THEN RETURN false; END IF;
 FOR head IN SELECT value FROM jsonb_array_elements(capsule.ancestor_heads) LOOP
  SELECT n.*,h.revision,h.status,h.valid_from,h.valid_until,v.bid_id AS owner_bid INTO node FROM retrieval.source_nodes n
   LEFT JOIN retrieval.source_heads h ON h.source_id=n.id LEFT JOIN design.document_versions v ON v.id=n.document_version_id WHERE n.id=(head->>'id')::uuid;
  IF NOT FOUND OR node.owner_bid IS DISTINCT FROM ctx.bid_id OR node.kind<>'document' OR node.policy_class<>'prebid_content' OR node.status IS DISTINCT FROM 'published'
   OR (node.document_version_id IS DISTINCT FROM ctx.body_version_id AND NOT EXISTS(SELECT FROM design.material_assignments m WHERE m.snapshot_id=ctx.material_snapshot_id AND m.bid_id=ctx.bid_id AND m.document_version_id=node.document_version_id AND m.role IN ('tender','supplement')))
   OR node.known_at>ctx.as_of OR node.valid_from>ctx.as_of OR node.valid_until<=ctx.as_of
   OR node.known_at>clock_timestamp() OR node.valid_from>clock_timestamp() OR node.valid_until<=clock_timestamp()
   OR node.document_version_id IS DISTINCT FROM (head->>'document_version_id')::uuid OR node.digest IS DISTINCT FROM head->>'digest'
   OR node.kind IS DISTINCT FROM head->>'kind' OR node.policy_class IS DISTINCT FROM head->>'policy_class'
   OR node.known_at IS DISTINCT FROM (head->>'known_at')::timestamptz OR node.revision IS DISTINCT FROM (head->>'revision')::bigint
   OR node.status IS DISTINCT FROM head->>'status' OR node.valid_from IS DISTINCT FROM (head->>'valid_from')::timestamptz OR node.valid_until IS DISTINCT FROM (head->>'valid_until')::timestamptz THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE FUNCTION score_execution.expected_current_chapter(target uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,score_execution,design,pg_temp AS $$
 SELECT jsonb_build_object('purpose','formal_score','rule',jsonb_build_object('id',r.id,'statement',r.statement,'conditions',r.conditions,'exceptions',r.exceptions,'maximum',r.max_points::text,
  'bands',(SELECT jsonb_agg(jsonb_build_object('id',r.id::text||':band:'||ordinal::text,'title',band->'label','description',band->'description','points',band->'points') ORDER BY ordinal) FROM jsonb_array_elements(r.bands) WITH ORDINALITY b(band,ordinal))),
  'body_version_id',ri.body_version_id,'parse_id',cp.parse_id,'method_digest','276a2c953aa86d935d46aabba432f7503ab6ce8081a9375b490ecb2172fd4d48','digest',cp.digest,
  'sections',jsonb_build_array(jsonb_build_object('id','full-body','pages',(SELECT jsonb_agg(p.physical_page ORDER BY p.physical_page) FROM design.pages p WHERE p.parse_id=cp.parse_id),
   'blocks',(SELECT jsonb_agg(jsonb_build_object('evidence_id',e.id,'physical_page',e.physical_page,'text',e.text_content,'digest',e.digest) ORDER BY e.physical_page,e.ordinal,e.id) FROM design.evidence_blocks e WHERE e.parse_id=cp.parse_id))))
 FROM design.chapter_packages cp JOIN design.review_runs rr ON rr.id=cp.review_id JOIN design.review_inputs ri ON ri.id=rr.input_id JOIN design.rule_items r ON r.id=cp.rule_id WHERE cp.id=target;
$$;

-- Private actual review-root bytes are bound to the server's immutable manifest blob.
-- They are never selected by the score-role clean-input projection.
CREATE FUNCTION score_execution.frozen_review_current(target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,design,pg_temp AS $$
DECLARE ctx record; root jsonb; doc jsonb; parsed record; expected jsonb; expected_ids uuid[]; actual_ids uuid[];
BEGIN
 SELECT i.*,ri.manifest,ri.body_version_id,ri.material_snapshot_id,b.digest AS blob_digest,b.byte_size
 INTO ctx FROM score_execution.inputs i JOIN design.review_runs rr ON rr.id=i.review_id AND rr.bid_id=i.bid_id
 JOIN design.review_inputs ri ON ri.id=rr.input_id AND ri.bid_id=i.bid_id
 JOIN design.blobs b ON b.id=ri.evidence_manifest_blob_id WHERE i.id=target;
 IF NOT FOUND OR ctx.review_root_digest IS DISTINCT FROM ctx.blob_digest
  OR octet_length(ctx.review_root_content) IS DISTINCT FROM ctx.byte_size
  OR ctx.manifest->>'evidence_manifest_digest' IS DISTINCT FROM ctx.blob_digest::text
  OR encode(sha256(convert_to(ctx.review_root_content,'UTF8')),'hex') IS DISTINCT FROM ctx.blob_digest::text THEN RETURN false; END IF;
 root:=ctx.review_root_content::jsonb;
 IF root->'identity' IS DISTINCT FROM ctx.identity_snapshot OR root->'confirmed_facts' IS DISTINCT FROM '[]'::jsonb
  OR root->'fact_set_id' IS DISTINCT FROM 'null'::jsonb OR ctx.manifest->'fact_set_id' IS DISTINCT FROM 'null'::jsonb
  OR jsonb_typeof(root->'rules') IS DISTINCT FROM 'array' OR jsonb_typeof(root->'documents') IS DISTINCT FROM 'array'
  OR root->'rule_set' IS DISTINCT FROM (SELECT to_jsonb(r) FROM design.rule_sets r WHERE r.id=(ctx.manifest->>'rule_set_id')::uuid AND r.bid_id=ctx.bid_id AND r.material_id=ctx.material_snapshot_id AND r.coverage='complete')
 THEN RETURN false; END IF;
 -- Compare the complete frozen rule rows, including provenance, conditions and evidence.
 SELECT jsonb_agg((to_jsonb(r)-'max_points')||jsonb_build_object('max_points',CASE WHEN r.max_points IS NULL THEN NULL ELSE r.max_points::text END) ORDER BY r.id)
 INTO expected FROM design.rule_items r WHERE r.rule_set_id=(ctx.manifest->>'rule_set_id')::uuid AND r.bid_id=ctx.bid_id;
 IF expected IS DISTINCT FROM (SELECT jsonb_agg(rule ORDER BY (rule->>'id')::uuid) FROM jsonb_array_elements(root->'rules') rule) THEN RETURN false; END IF;
 SELECT array_agg(document_version_id ORDER BY document_version_id) INTO expected_ids FROM (
  SELECT ctx.body_version_id AS document_version_id UNION SELECT a.document_version_id FROM design.material_assignments a WHERE a.bid_id=ctx.bid_id AND a.snapshot_id=ctx.material_snapshot_id AND a.role IN ('tender','supplement')
 ) required;
 SELECT array_agg((d->>'document_version_id')::uuid ORDER BY (d->>'document_version_id')::uuid) INTO actual_ids FROM jsonb_array_elements(root->'documents') d;
 IF expected_ids IS DISTINCT FROM actual_ids THEN RETURN false; END IF;
 FOR doc IN SELECT * FROM jsonb_array_elements(root->'documents') LOOP
  SELECT p.*,v.blob_id,v.obtained_at,b.digest AS blob_digest,b.byte_size INTO parsed FROM design.document_versions v JOIN design.blobs b ON b.id=v.blob_id
  JOIN LATERAL(SELECT * FROM design.parse_runs pr WHERE pr.document_version_id=v.id AND pr.bid_id=v.bid_id ORDER BY pr.created_at DESC,pr.id DESC LIMIT 1)p ON true
  WHERE v.bid_id=ctx.bid_id AND v.id=(doc->>'document_version_id')::uuid;
  IF NOT FOUND OR doc->>'parse_id' IS DISTINCT FROM parsed.id::text OR doc->>'blob_id' IS DISTINCT FROM parsed.blob_id::text
   OR doc->>'digest' IS DISTINCT FROM parsed.blob_digest::text OR (doc->>'byte_size')::bigint IS DISTINCT FROM parsed.byte_size
   OR (doc->>'obtained_at')::timestamptz IS DISTINCT FROM parsed.obtained_at
   OR doc->>'parser_digest' IS DISTINCT FROM parsed.parser_digest::text OR doc->>'config_digest' IS DISTINCT FROM parsed.config_digest::text
   OR (doc->>'page_count')::integer IS DISTINCT FROM parsed.page_count OR doc->>'coverage' IS DISTINCT FROM parsed.coverage
   OR doc->'gaps' IS DISTINCT FROM parsed.gaps OR parsed.coverage<>'complete' OR parsed.gaps<>'[]'::jsonb OR parsed.page_count<1
   OR (doc->>'document_version_id'=ctx.body_version_id::text AND doc->>'role' IS DISTINCT FROM 'body')
   OR (doc->>'document_version_id'<>ctx.body_version_id::text AND NOT EXISTS(SELECT FROM design.material_assignments a WHERE a.bid_id=ctx.bid_id AND a.snapshot_id=ctx.material_snapshot_id AND a.document_version_id=(doc->>'document_version_id')::uuid AND a.role=doc->>'role' AND a.role IN ('tender','supplement')))
  THEN RETURN false; END IF;
  IF (SELECT count(*) FROM design.pages page WHERE page.parse_id=parsed.id)<>parsed.page_count
   OR EXISTS(SELECT FROM design.pages page WHERE page.parse_id=parsed.id AND (page.physical_page NOT BETWEEN 1 AND parsed.page_count OR page.quality<>'usable' OR page.gaps<>'[]'::jsonb)) THEN RETURN false; END IF;
  SELECT jsonb_agg(jsonb_build_object('physical_page',physical_page,'quality',quality,'gaps',gaps) ORDER BY physical_page) INTO expected FROM design.pages WHERE bid_id=ctx.bid_id AND parse_id=parsed.id;
  IF doc->'pages' IS DISTINCT FROM expected THEN RETURN false; END IF;
  SELECT jsonb_agg(jsonb_build_object('id',id,'physical_page',physical_page,'ordinal',ordinal,'kind',kind,'text_content',text_content,'bbox',bbox,'structure',structure,'digest',digest) ORDER BY physical_page,ordinal) INTO expected FROM design.evidence_blocks WHERE bid_id=ctx.bid_id AND parse_id=parsed.id;
  IF doc->'blocks' IS DISTINCT FROM expected THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE FUNCTION score_execution.current_input(target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,historical_input,design,pg_temp AS $$
DECLARE i score_execution.inputs; member record; expected_ids uuid[]; actual_ids uuid[]; identity record; content jsonb;
BEGIN
 i:=score_execution.lock_input(target);
 PERFORM pg_advisory_xact_lock(90127,1);
 IF NOT EXISTS(SELECT FROM jsonb_object_keys(i.price_config) key WHERE key='profile_digest')
  OR (SELECT count(*) FROM jsonb_object_keys(i.price_config))<>6
  OR coalesce((i.price_config->>'input_per_million')::numeric,0)<=0 OR coalesce((i.price_config->>'output_per_million')::numeric,0)<=0
  OR (i.price_config->>'input_per_million')::numeric IN ('NaN','Infinity','-Infinity') OR (i.price_config->>'output_per_million')::numeric IN ('NaN','Infinity','-Infinity') THEN RETURN false; END IF;
 IF i.historical_input_id IS NOT NULL THEN
  RETURN EXISTS(SELECT FROM historical_backtest.inputs h WHERE h.id=i.historical_input_id AND h.bid_id=i.bid_id AND h.origin=i.origin AND h.method_digest=i.method_digest)
   AND NOT EXISTS(SELECT FROM score_execution.current_chapters WHERE input_id=target) AND historical_input.current_input(i.historical_input_id);
 END IF;
 IF EXISTS(SELECT FROM design.bid_profiles p WHERE p.bid_id=i.bid_id AND (p.archived_at IS NOT NULL OR p.phase IN ('closed','terminated')))
  OR NOT score_execution.frozen_review_current(target) THEN RETURN false; END IF;
 SELECT profile.*,r.engineering_project_id,r.lot,r.round_no,b.name AS bidder_name,p.title AS engineering_title,p.region,p.engineering_type,
  authority.id AS authority_id,authority.payload AS identity_record
 INTO identity FROM design.bid_profiles profile JOIN design.bid_rounds r ON r.id=profile.round_id JOIN design.bidders b ON b.id=profile.bidder_id JOIN design.engineering_projects p ON p.id=r.engineering_project_id
 JOIN LATERAL (SELECT id,payload FROM design.audit_entries WHERE bid_id=profile.bid_id AND event_type='identity.bound' ORDER BY coalesce((payload->>'identity_epoch')::bigint,0) DESC,created_at DESC,id DESC LIMIT 1) authority ON true WHERE profile.bid_id=i.bid_id;
 IF NOT FOUND OR identity.current_review_id IS DISTINCT FROM i.review_id OR i.identity_snapshot->>'binding_status' IS DISTINCT FROM 'confirmed'
  OR i.identity_snapshot->>'authority_id' IS DISTINCT FROM identity.authority_id::text OR identity.identity_record->'confirmed' IS DISTINCT FROM 'true'::jsonb
  OR identity.identity_record->'after' IS DISTINCT FROM jsonb_build_object('bidder_id',identity.bidder_id,'bidder_name',identity.bidder_name,'round_id',identity.round_id,'engineering_project_id',identity.engineering_project_id,'engineering_title',identity.engineering_title,'region',identity.region,'engineering_type',identity.engineering_type,'lot',identity.lot,'round_no',identity.round_no)
  OR EXISTS(SELECT FROM jsonb_each(identity.identity_record->'after') field WHERE i.identity_snapshot->field.key IS DISTINCT FROM field.value)
  OR NOT EXISTS(SELECT FROM design.review_runs rr JOIN design.review_inputs ri ON ri.id=rr.input_id JOIN design.audit_entries authority ON authority.id=identity.authority_id WHERE rr.id=i.review_id AND authority.created_at<=ri.frozen_at) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM design.review_runs rr JOIN design.review_inputs ri ON ri.id=rr.input_id WHERE rr.id=i.review_id AND ri.manifest->'fact_set_id' IS DISTINCT FROM 'null'::jsonb AND ri.manifest ? 'fact_set_id') THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM design.material_assignments a WHERE a.snapshot_id=identity.current_material_id AND a.role NOT IN ('tender','supplement','body')) THEN RETURN false; END IF;
 SELECT array_agg(document_version_id ORDER BY document_version_id) INTO expected_ids FROM (
  SELECT identity.current_body_id AS document_version_id UNION SELECT a.document_version_id FROM design.material_assignments a WHERE a.snapshot_id=identity.current_material_id AND a.role IN ('tender','supplement')
 ) required_documents;
 SELECT array_agg(document_version_id ORDER BY document_version_id) INTO actual_ids FROM (
  SELECT DISTINCT (b->>'document_version_id')::uuid AS document_version_id FROM score_execution.current_chapters cc JOIN design.research_chapter_authorities authority ON authority.chapter_package_id=cc.chapter_package_id CROSS JOIN LATERAL jsonb_array_elements(authority.bindings) b WHERE cc.input_id=target
 ) bound_documents;
 IF actual_ids IS DISTINCT FROM expected_ids THEN RETURN false; END IF;
 SELECT array_agg(r.id ORDER BY r.id) INTO expected_ids FROM design.review_runs rr JOIN design.review_inputs ri ON ri.id=rr.input_id JOIN design.rule_items r ON r.rule_set_id=(ri.manifest->>'rule_set_id')::uuid AND r.bid_id=i.bid_id AND r.kind='scoring' WHERE rr.id=i.review_id AND rr.bid_id=i.bid_id;
 SELECT array_agg(cp.rule_id ORDER BY cp.rule_id) INTO actual_ids FROM score_execution.current_chapters c JOIN design.chapter_packages cp ON cp.id=c.chapter_package_id WHERE c.input_id=target AND cp.bid_id=i.bid_id AND cp.review_id=i.review_id;
 IF expected_ids IS NULL OR actual_ids IS DISTINCT FROM expected_ids OR cardinality(expected_ids) NOT BETWEEN 1 AND 1000 THEN RETURN false; END IF;
 FOR member IN SELECT c.*,cp.bid_id,cp.review_id,cp.rule_id,r.max_points,r.bands FROM score_execution.current_chapters c JOIN design.chapter_packages cp ON cp.id=c.chapter_package_id JOIN design.rule_items r ON r.id=cp.rule_id WHERE c.input_id=target LOOP
  IF member.bid_id IS DISTINCT FROM i.bid_id OR member.review_id IS DISTINCT FROM i.review_id OR NOT score_execution.current_chapter(member.chapter_package_id)
   OR member.max_points IS NULL OR member.max_points<=0 OR member.max_points IN ('NaN','Infinity','-Infinity') OR jsonb_typeof(member.bands) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
  IF jsonb_array_length(member.bands) NOT BETWEEN 1 AND 100 OR EXISTS(SELECT FROM jsonb_array_elements(member.bands) band WHERE jsonb_typeof(band->'label') IS DISTINCT FROM 'string' OR jsonb_typeof(band->'description') IS DISTINCT FROM 'string' OR length(btrim(band->>'label'))=0 OR length(btrim(band->>'description'))=0 OR NOT historical_input.valid_band_points(band->'points',member.max_points)) THEN RETURN false; END IF;
  content:=score_execution.expected_current_chapter(member.chapter_package_id);
  IF content IS NULL OR content IS DISTINCT FROM member.raw_content::jsonb OR NOT historical_input.clean_structure(content) OR length(member.raw_content)>250000 THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

-- Private clean projection. The worker cannot call this helper with arbitrary IDs.
CREATE FUNCTION score_execution.input_packages(target uuid) RETURNS TABLE(chapter_id uuid,package_digest design.sha256,raw_content text,rule_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,score_execution,historical_backtest,historical_input,design,pg_temp AS $$
 SELECT c.id,c.digest,p.raw_content,c.rule_id FROM score_execution.inputs i JOIN historical_backtest.chapters c ON c.input_id=i.historical_input_id JOIN historical_input.chapter_payloads p ON p.chapter_id=c.id WHERE i.id=target
 UNION ALL
 SELECT c.chapter_package_id,cp.digest,c.raw_content,cp.rule_id FROM score_execution.current_chapters c JOIN design.chapter_packages cp ON cp.id=c.chapter_package_id WHERE c.input_id=target;
$$;
CREATE FUNCTION score_execution.seal_input(target uuid,actor uuid) RETURNS design.sha256
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,historical_input,design,pg_temp AS $$
DECLARE i score_execution.inputs; value jsonb; result text; semantic text; rules integer;
BEGIN
 i:=score_execution.lock_input(target);
 IF actor IS NULL OR i.actor_id IS DISTINCT FROM actor OR NOT score_execution.current_input(target) THEN RAISE EXCEPTION 'score_input_unavailable' USING ERRCODE='55000'; END IF;
 IF i.historical_input_id IS NOT NULL THEN SELECT jsonb_build_object('input',to_jsonb(i),'historical_authority',h.authority_digest),h.rule_count INTO value,rules FROM historical_input.seals h WHERE h.input_id=i.historical_input_id;
 ELSE SELECT jsonb_build_object('input',to_jsonb(i),'chapters',jsonb_agg(to_jsonb(c) ORDER BY c.chapter_package_id)),count(*) INTO value,rules FROM score_execution.current_chapters c WHERE c.input_id=target; END IF;
 result:=encode(sha256(convert_to(value::text,'UTF8')),'hex');
 SELECT encode(sha256(convert_to(jsonb_build_object('method',i.method_digest,'packages',jsonb_agg(package_digest ORDER BY package_digest))::text,'UTF8')),'hex') INTO semantic FROM score_execution.input_packages(target);
 INSERT INTO score_execution.input_seals VALUES(target,result,semantic,rules,clock_timestamp());
 RETURN result::design.sha256;
END $$;
CREATE FUNCTION score_execution.prevent_sealed_append() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,pg_temp AS $$
BEGIN
 PERFORM score_execution.lock_input(NEW.input_id);
 IF EXISTS(SELECT FROM score_execution.input_seals WHERE input_id=NEW.input_id) THEN RAISE EXCEPTION 'score_input_sealed' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER sealed_guard BEFORE INSERT ON score_execution.current_chapters FOR EACH ROW EXECUTE FUNCTION score_execution.prevent_sealed_append();

CREATE FUNCTION score_execution.start_job(target uuid,ceiling numeric,actor uuid,command uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,design,pg_temp AS $$
DECLARE i score_execution.inputs; identity uuid:=gen_random_uuid(); minimum numeric; semantic design.sha256;
BEGIN
 i:=score_execution.lock_input(target);
 IF actor IS NULL OR command IS NULL OR ceiling IS NULL OR ceiling<=0 OR ceiling IN ('NaN','Infinity','-Infinity')
  OR NOT EXISTS(SELECT FROM design.commands c WHERE c.id=command AND c.actor_id=actor AND c.target_id=target)
  OR EXISTS(SELECT FROM score_execution.calls call WHERE call.profile_digest=i.method_digest AND call.chapter_digest IN (SELECT package_digest FROM score_execution.input_packages(target)) AND call.state IN ('reserved','unknown','not_sent'))
  OR NOT score_execution.current_input(target) THEN RAISE EXCEPTION 'score_job_invalid' USING ERRCODE='55000'; END IF;
 SELECT rule_count*3*((i.price_config->>'context_tokens')::numeric*(i.price_config->>'input_per_million')::numeric+(i.price_config->>'max_output_tokens')::numeric*(i.price_config->>'output_per_million')::numeric)/1000000 INTO minimum FROM score_execution.input_seals WHERE input_id=target;
 IF minimum IS NULL OR ceiling<minimum THEN RAISE EXCEPTION 'score_budget_insufficient' USING ERRCODE='22023'; END IF;
 SELECT execution_key INTO semantic FROM score_execution.input_seals WHERE input_id=target;
 INSERT INTO score_execution.jobs(id,input_id,execution_key,command_id,created_by,state,budget) VALUES(identity,target,semantic,command,actor,'queued',ceiling);
 RETURN identity;
END $$;

CREATE FUNCTION score_execution.assert_lease(target uuid,expected_generation bigint,expected_fence bigint,expected_owner uuid,require_current boolean) RETURNS score_execution.jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,pg_temp AS $$
DECLARE job score_execution.jobs;
BEGIN
 IF expected_generation IS NULL OR expected_fence IS NULL OR expected_owner IS NULL OR require_current IS NULL THEN RAISE EXCEPTION 'score_lease_invalid' USING ERRCODE='55000'; END IF;
 SELECT * INTO job FROM score_execution.jobs WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'score_job_missing' USING ERRCODE='P0002'; END IF;
 PERFORM score_execution.lock_input(job.input_id);
 IF require_current AND NOT score_execution.current_input(job.input_id) THEN RAISE EXCEPTION 'score_source_changed' USING ERRCODE='55000'; END IF;
 -- Re-read current generation AFTER any scope wait; never return an old job snapshot.
 SELECT * INTO job FROM score_execution.jobs WHERE id=target FOR UPDATE;
 IF job.state<>'running' OR job.generation IS DISTINCT FROM expected_generation OR job.fence IS DISTINCT FROM expected_fence
  OR job.owner_token IS DISTINCT FROM expected_owner OR job.lease_until<=clock_timestamp() THEN RAISE EXCEPTION 'score_lease_invalid' USING ERRCODE='55000'; END IF;
 RETURN job;
END $$;
CREATE FUNCTION score_execution.claim_job(target uuid,expected_generation bigint,worker uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,pg_temp AS $$
DECLARE job score_execution.jobs;
BEGIN
 IF worker IS NULL OR expected_generation IS NULL THEN RAISE EXCEPTION 'score_lease_invalid' USING ERRCODE='55000'; END IF;
 SELECT * INTO job FROM score_execution.jobs WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'score_job_missing' USING ERRCODE='P0002'; END IF;
 PERFORM score_execution.lock_input(job.input_id);
 IF NOT score_execution.current_input(job.input_id) THEN RAISE EXCEPTION 'score_source_changed' USING ERRCODE='55000'; END IF;
 SELECT * INTO job FROM score_execution.jobs WHERE id=target FOR UPDATE;
 IF job.generation IS DISTINCT FROM expected_generation OR job.state NOT IN ('queued','running') OR (job.state='running' AND job.lease_until>clock_timestamp()) THEN RAISE EXCEPTION 'score_not_claimable' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT FROM score_execution.calls WHERE job_id=target AND state IN ('reserved','unknown')) THEN RAISE EXCEPTION 'score_billing_reconciliation_required' USING ERRCODE='55000'; END IF;
 UPDATE score_execution.jobs SET state='running',fence=fence+1,owner_token=worker,lease_until=clock_timestamp()+interval '90 seconds' WHERE id=target RETURNING * INTO job;
 RETURN jsonb_build_object('job_id',job.id,'generation',job.generation,'fence',job.fence,'owner',worker);
END $$;
CREATE FUNCTION score_execution.heartbeat(target uuid,generation bigint,fence bigint,worker uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,pg_temp AS $$
BEGIN
 PERFORM score_execution.assert_lease(target,generation,fence,worker,false);
 UPDATE score_execution.jobs SET lease_until=clock_timestamp()+interval '90 seconds' WHERE id=target;
END $$;
CREATE FUNCTION score_execution.owned_input(target uuid,generation bigint,fence bigint,worker uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,historical_input,historical_backtest,design,pg_temp AS $$
DECLARE job score_execution.jobs; i score_execution.inputs; chapters jsonb; rules jsonb;
BEGIN
 job:=score_execution.assert_lease(target,generation,fence,worker,true);
 SELECT * INTO i FROM score_execution.inputs WHERE id=job.input_id;
 IF i.historical_input_id IS NOT NULL THEN
  SELECT jsonb_agg(jsonb_build_object('id',c.id,'package',p.raw_content::jsonb) ORDER BY c.rule_id),jsonb_agg(c.rule_id ORDER BY c.rule_id) INTO chapters,rules FROM historical_backtest.chapters c JOIN historical_input.chapter_payloads p ON p.chapter_id=c.id WHERE c.input_id=i.historical_input_id;
 ELSE
  SELECT jsonb_agg(jsonb_build_object('id',c.chapter_package_id,'package',c.raw_content::jsonb) ORDER BY cp.rule_id),jsonb_agg(cp.rule_id ORDER BY cp.rule_id) INTO chapters,rules FROM score_execution.current_chapters c JOIN design.chapter_packages cp ON cp.id=c.chapter_package_id WHERE c.input_id=i.id;
 END IF;
 RETURN jsonb_build_object('execution_purpose','score','method_digest',i.method_digest,'input_digest',(SELECT digest FROM score_execution.input_seals WHERE input_id=i.id),
  'chapters',chapters,'necessary_rule_ids',rules,'price_config',i.price_config,'technical_maximum',i.technical_maximum,'maximum_verified',i.maximum_verified);
END $$;

CREATE FUNCTION score_execution.reserve_call(target uuid,generation bigint,fence bigint,worker uuid,chapter uuid,replicate_number integer,request_sha design.sha256,profile_sha design.sha256,ceiling numeric) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,historical_backtest,design,pg_temp AS $$
DECLARE job score_execution.jobs; i score_execution.inputs; old score_execution.calls; reserve numeric; identity uuid:=gen_random_uuid(); package_sha design.sha256;
BEGIN
 job:=score_execution.assert_lease(target,generation,fence,worker,true);
 SELECT * INTO i FROM score_execution.inputs WHERE id=job.input_id;
 SELECT package_digest INTO package_sha FROM score_execution.input_packages(i.id) WHERE chapter_id=chapter;
 IF chapter IS NULL OR package_sha IS NULL OR replicate_number IS NULL OR replicate_number NOT BETWEEN 1 AND 3 OR request_sha IS NULL OR profile_sha IS DISTINCT FROM i.method_digest OR ceiling IS NULL THEN RAISE EXCEPTION 'score_call_identity_invalid' USING ERRCODE='22023'; END IF;
 reserve:=((i.price_config->>'context_tokens')::numeric*(i.price_config->>'input_per_million')::numeric+(i.price_config->>'max_output_tokens')::numeric*(i.price_config->>'output_per_million')::numeric)/1000000;
 IF ceiling IS DISTINCT FROM reserve THEN RAISE EXCEPTION 'score_budget_profile_mismatch' USING ERRCODE='22023'; END IF;
 -- assert_lease already holds leading 91143, so every global logical lookup/insert is
 -- serialized even across equivalent inputs/jobs. Whole-scope changes cannot conceal
 -- a still-unknown shared chapter. Random input/job/command IDs never authorize retry.
 SELECT * INTO old FROM score_execution.calls WHERE profile_digest=profile_sha AND chapter_digest=package_sha AND replicate=replicate_number FOR UPDATE;
 IF FOUND THEN
  IF old.request_digest IS DISTINCT FROM request_sha OR old.state<>'succeeded' THEN RAISE EXCEPTION 'score_call_reconciliation_required' USING ERRCODE='55000'; END IF;
  INSERT INTO score_execution.call_bindings VALUES(target,chapter,replicate_number,old.id,old.job_id<>target) ON CONFLICT(job_id,chapter_id,replicate) DO NOTHING;
  IF NOT EXISTS(SELECT FROM score_execution.call_bindings bind WHERE bind.job_id=target AND bind.chapter_id=chapter AND bind.replicate=replicate_number AND bind.call_id=old.id) THEN RAISE EXCEPTION 'score_call_binding_changed' USING ERRCODE='23514'; END IF;
  RETURN jsonb_build_object('new_reservation',false,'call_id',old.id);
 END IF;
 IF EXISTS(SELECT FROM score_execution.call_bindings bind JOIN score_execution.calls call ON call.id=bind.call_id WHERE bind.job_id=target AND call.state<>'succeeded') THEN RAISE EXCEPTION 'score_call_reconciliation_required' USING ERRCODE='55000'; END IF;
 IF job.estimated_fee+job.reserved_fee+reserve>job.budget THEN RAISE EXCEPTION 'score_budget_exceeded' USING ERRCODE='55000'; END IF;
 INSERT INTO score_execution.calls(id,job_id,chapter_id,replicate,chapter_digest,request_digest,profile_digest,owner_token,state,reserved_fee) VALUES(identity,target,chapter,replicate_number,package_sha,request_sha,profile_sha,worker,'reserved',reserve);
 INSERT INTO score_execution.call_bindings VALUES(target,chapter,replicate_number,identity,false);
 UPDATE score_execution.jobs SET reserved_fee=reserved_fee+reserve WHERE id=target;
 RETURN jsonb_build_object('new_reservation',true,'call_id',identity);
END $$;
-- Accounting deliberately does NOT require current source or a live lease: genuine usage
-- must settle even if a source is withdrawn or a lease expires during HTTP. The original
-- call's owner token remains necessary; callers cannot invent a new call or reset unknown.
CREATE FUNCTION score_execution.settle_call(identity uuid,worker uuid,state_name text,raw_response text,estimate numeric) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,design,pg_temp AS $$
DECLARE call score_execution.calls; job score_execution.jobs; response jsonb; usage jsonb; i score_execution.inputs; calculated numeric;
BEGIN
 SELECT * INTO call FROM score_execution.calls WHERE id=identity;
 IF NOT FOUND OR worker IS NULL OR call.owner_token IS DISTINCT FROM worker OR state_name IS NULL OR state_name NOT IN ('succeeded','not_sent','unknown') THEN RAISE EXCEPTION 'score_settlement_invalid' USING ERRCODE='22023'; END IF;
 SELECT * INTO job FROM score_execution.jobs WHERE id=call.job_id FOR UPDATE;
 SELECT * INTO call FROM score_execution.calls WHERE id=identity FOR UPDATE;
 IF call.state<>'reserved' THEN RAISE EXCEPTION 'score_call_already_settled' USING ERRCODE='55000'; END IF;
 IF state_name='succeeded' THEN
  IF raw_response IS NULL OR octet_length(raw_response)>2097152 OR estimate IS NULL OR estimate<0 OR estimate IN ('NaN','Infinity','-Infinity') THEN RAISE EXCEPTION 'score_response_invalid' USING ERRCODE='22023'; END IF;
  response:=raw_response::jsonb;usage:=response->'usage';
  SELECT * INTO i FROM score_execution.inputs WHERE id=job.input_id;
  IF NOT score_execution.exact_keys(response,ARRAY['schema','execution_purpose','data','content','usage','model','request_id','response_id','finish_reason','request_digest','profile_digest','formal_scoring_eligible','fee_basis']) OR response->>'schema' IS DISTINCT FROM 'controlled-score-response/1' OR response->>'execution_purpose' IS DISTINCT FROM 'score'
   OR response->>'request_digest' IS DISTINCT FROM call.request_digest::text OR response->>'profile_digest' IS DISTINCT FROM call.profile_digest::text
   OR response->>'model' IS DISTINCT FROM 'deepseek-flash' OR response->>'fee_basis' IS DISTINCT FROM 'usage_price_estimate' OR response->'formal_scoring_eligible' IS DISTINCT FROM 'false'::jsonb
   OR jsonb_typeof(usage->'prompt_tokens') IS DISTINCT FROM 'number' OR jsonb_typeof(usage->'completion_tokens') IS DISTINCT FROM 'number' OR jsonb_typeof(usage->'total_tokens') IS DISTINCT FROM 'number'
   OR (usage->>'prompt_tokens')::numeric<>trunc((usage->>'prompt_tokens')::numeric) OR (usage->>'completion_tokens')::numeric<>trunc((usage->>'completion_tokens')::numeric)
   OR (usage->>'prompt_tokens')::numeric<0 OR (usage->>'completion_tokens')::numeric<0 OR (usage->>'total_tokens')::numeric<>(usage->>'prompt_tokens')::numeric+(usage->>'completion_tokens')::numeric THEN RAISE EXCEPTION 'score_usage_invalid' USING ERRCODE='23514'; END IF;
  calculated:=((usage->>'prompt_tokens')::numeric*(i.price_config->>'input_per_million')::numeric+(usage->>'completion_tokens')::numeric*(i.price_config->>'output_per_million')::numeric)/1000000;
  IF estimate IS DISTINCT FROM calculated OR calculated>call.reserved_fee THEN RAISE EXCEPTION 'score_usage_exceeds_reserve' USING ERRCODE='23514'; END IF;
 ELSIF raw_response IS NOT NULL OR (state_name='not_sent' AND estimate IS DISTINCT FROM 0::numeric) OR (state_name='unknown' AND estimate IS NOT NULL) THEN RAISE EXCEPTION 'score_settlement_invalid' USING ERRCODE='22023'; END IF;
 UPDATE score_execution.calls SET state=state_name,response=raw_response,response_digest=CASE WHEN raw_response IS NULL THEN NULL ELSE encode(sha256(convert_to(raw_response,'UTF8')),'hex')::design.sha256 END,estimated_fee=estimate,settled_at=clock_timestamp() WHERE id=identity;
 IF state_name<>'unknown' THEN UPDATE score_execution.jobs SET reserved_fee=reserved_fee-call.reserved_fee,estimated_fee=estimated_fee+estimate WHERE id=job.id;
 END IF;
 IF state_name IN ('unknown','not_sent') THEN UPDATE score_execution.jobs SET state=CASE WHEN state IN ('cancelled','failed') THEN state ELSE 'paused' END,owner_token=NULL,lease_until=NULL WHERE id=job.id; END IF;
END $$;
CREATE FUNCTION score_execution.success_response(target uuid,generation bigint,fence bigint,worker uuid,identity uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,pg_temp AS $$
BEGIN
 PERFORM score_execution.assert_lease(target,generation,fence,worker,true);
 RETURN (SELECT call.response::jsonb FROM score_execution.calls call JOIN score_execution.call_bindings bind ON bind.call_id=call.id WHERE bind.job_id=target AND call.id=identity AND call.state='succeeded');
END $$;

CREATE FUNCTION score_execution.publish_result(target uuid,generation bigint,fence bigint,worker uuid,document jsonb) RETURNS design.sha256
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,design,pg_temp AS $$
DECLARE job score_execution.jobs; expected_count integer; item jsonb; judgment jsonb; citation jsonb; call score_execution.calls; seen uuid[]:='{}'; seen_chapters uuid[]:='{}'; item_chapter uuid; input_sha design.sha256; result text; package jsonb; i score_execution.inputs;
BEGIN
 job:=score_execution.assert_lease(target,generation,fence,worker,true);
 SELECT rule_count,digest INTO expected_count,input_sha FROM score_execution.input_seals WHERE input_id=job.input_id;
 SELECT * INTO i FROM score_execution.inputs WHERE id=job.input_id;
 IF NOT score_execution.exact_keys(document,ARRAY['schema','execution_purpose','input_digest','method_digest','items','features','prediction','formal_eligibility'])
  OR NOT score_execution.exact_keys(document->'prediction',ARRAY['mode','value','intervals','reasons'])
  OR NOT score_execution.exact_keys(document->'features',ARRAY['schema','status','values','reasons']) OR document->>'schema' IS DISTINCT FROM 'complete-score-judgments/1'
  OR document->>'execution_purpose' IS DISTINCT FROM 'score' OR document->>'input_digest' IS DISTINCT FROM input_sha::text
  OR document->>'method_digest' IS DISTINCT FROM '276a2c953aa86d935d46aabba432f7503ab6ce8081a9375b490ecb2172fd4d48'
  OR document->>'formal_eligibility' IS DISTINCT FROM 'unavailable' OR document->'prediction'->>'mode' IS DISTINCT FROM 'unavailable'
  OR document->'prediction'->'value' IS DISTINCT FROM 'null'::jsonb OR document->'prediction'->'intervals' IS DISTINCT FROM 'null'::jsonb
  OR document->'features'->>'schema' IS DISTINCT FROM 'complete-three-band-features/1' OR document->'features'->>'status' IS DISTINCT FROM 'unavailable' OR document->'features'->'values' IS DISTINCT FROM 'null'::jsonb
  OR jsonb_typeof(document->'items') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'score_result_invalid' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(document->'items')<>expected_count OR (SELECT count(*) FROM score_execution.call_bindings bind JOIN score_execution.calls call ON call.id=bind.call_id WHERE bind.job_id=target AND call.state='succeeded')<>expected_count*3 THEN RAISE EXCEPTION 'score_three_complete_required' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(document->'items') LOOP
  IF i.historical_input_id IS NOT NULL THEN
   SELECT c.id,p.raw_content::jsonb INTO item_chapter,package FROM historical_backtest.chapters c JOIN historical_input.chapter_payloads p ON p.chapter_id=c.id WHERE c.input_id=i.historical_input_id AND c.digest::text=item->>'package_digest';
  ELSE
   SELECT c.chapter_package_id,c.raw_content::jsonb INTO item_chapter,package FROM score_execution.current_chapters c JOIN design.chapter_packages cp ON cp.id=c.chapter_package_id WHERE c.input_id=i.id AND cp.digest::text=item->>'package_digest';
  END IF;
  IF NOT FOUND OR item_chapter=ANY(seen_chapters) THEN RAISE EXCEPTION 'score_package_scope_invalid' USING ERRCODE='23514'; END IF;
  seen_chapters:=array_append(seen_chapters,item_chapter);
  IF NOT score_execution.exact_keys(item,ARRAY['package_digest','valid_replicates','judgments','prediction']) OR NOT score_execution.exact_keys(item->'prediction',ARRAY['mode','reasons']) OR item->'prediction'->>'mode' IS DISTINCT FROM 'unavailable' OR item->'valid_replicates' IS DISTINCT FROM '3'::jsonb OR jsonb_typeof(item->'judgments') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'score_three_complete_required' USING ERRCODE='23514'; END IF;
  IF jsonb_array_length(item->'judgments')<>3 THEN RAISE EXCEPTION 'score_three_complete_required' USING ERRCODE='23514'; END IF;
  FOR judgment IN SELECT value FROM jsonb_array_elements(item->'judgments') LOOP
   SELECT c.* INTO call FROM score_execution.calls c JOIN score_execution.call_bindings bind ON bind.call_id=c.id WHERE c.id=(judgment->>'call_id')::uuid AND bind.job_id=target AND bind.chapter_id=item_chapter AND bind.replicate=c.replicate AND c.state='succeeded';
   IF NOT FOUND OR NOT score_execution.exact_keys(judgment,ARRAY['replicate','call_id','payload']) OR NOT score_execution.exact_keys(judgment->'payload',ARRAY['band_id','reason','coverage','status','evidence']) OR call.chapter_digest::text IS DISTINCT FROM package->>'digest' OR call.id=ANY(seen) OR judgment->'replicate' IS DISTINCT FROM to_jsonb(call.replicate) OR judgment->'payload' IS DISTINCT FROM call.response::jsonb->'data'
    OR judgment->'payload'->>'status' IS DISTINCT FROM 'valid' OR judgment->'payload'->>'coverage' IS DISTINCT FROM 'complete'
    OR coalesce(length(btrim(judgment->'payload'->>'reason')),0)=0 OR NOT EXISTS(SELECT FROM jsonb_array_elements(package->'rule'->'bands') band WHERE band->>'id'=judgment->'payload'->>'band_id')
    OR jsonb_typeof(judgment->'payload'->'evidence') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'score_judgment_invalid' USING ERRCODE='23514'; END IF;
   FOR citation IN SELECT value FROM jsonb_array_elements(judgment->'payload'->'evidence') LOOP
    IF NOT score_execution.exact_keys(citation,ARRAY['evidence_id','quote']) OR coalesce(length(citation->>'quote'),0)=0 OR NOT EXISTS(SELECT FROM jsonb_array_elements(package->'sections') section CROSS JOIN LATERAL jsonb_array_elements(section->'blocks') block WHERE block->>'evidence_id'=citation->>'evidence_id' AND position(citation->>'quote' IN block->>'text')>0) THEN RAISE EXCEPTION 'score_quote_invalid' USING ERRCODE='23514'; END IF;
   END LOOP;
   seen:=array_append(seen,call.id);
  END LOOP;
 END LOOP;
 result:=encode(sha256(convert_to(document::text,'UTF8')),'hex');
 INSERT INTO score_execution.results VALUES(target,input_sha,document,result,clock_timestamp());
 UPDATE score_execution.jobs SET state='succeeded',owner_token=NULL,lease_until=NULL,finished_at=clock_timestamp() WHERE id=target;
 RETURN result::design.sha256;
END $$;
CREATE FUNCTION score_execution.pause_failed(target uuid,generation bigint,fence bigint,worker uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,pg_temp AS $$
BEGIN
 PERFORM score_execution.assert_lease(target,generation,fence,worker,false);
 UPDATE score_execution.jobs SET state='paused',owner_token=NULL,lease_until=NULL WHERE id=target;
END $$;
CREATE FUNCTION score_execution.change_job(target uuid,expected_generation bigint,action_name text,actor uuid,reason_text text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,design,pg_temp AS $$
DECLARE job score_execution.jobs;
BEGIN
 IF expected_generation IS NULL OR action_name IS NULL OR actor IS NULL OR reason_text IS NULL OR action_name NOT IN ('pause','resume','cancel') OR length(btrim(reason_text)) NOT BETWEEN 1 AND 4000 THEN RAISE EXCEPTION 'score_job_command_invalid' USING ERRCODE='22023'; END IF;
 SELECT * INTO job FROM score_execution.jobs WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'score_job_missing' USING ERRCODE='P0002'; END IF;
 PERFORM score_execution.lock_input(job.input_id);
 IF action_name='resume' AND NOT score_execution.current_input(job.input_id) THEN RAISE EXCEPTION 'score_source_changed' USING ERRCODE='55000'; END IF;
 SELECT * INTO job FROM score_execution.jobs WHERE id=target FOR UPDATE;
 IF job.generation IS DISTINCT FROM expected_generation THEN RAISE EXCEPTION 'score_generation_changed' USING ERRCODE='40001'; END IF;
 IF job.state IN ('succeeded','cancelled','failed') OR (action_name='resume' AND job.state<>'paused') THEN RAISE EXCEPTION 'score_job_state_invalid' USING ERRCODE='55000'; END IF;
 IF action_name='resume' AND EXISTS(SELECT FROM score_execution.calls call JOIN score_execution.inputs i ON i.id=job.input_id WHERE call.profile_digest=i.method_digest AND call.chapter_digest IN (SELECT package_digest FROM score_execution.input_packages(i.id)) AND call.state<>'succeeded') THEN RAISE EXCEPTION 'score_billing_reconciliation_required' USING ERRCODE='55000'; END IF;
 UPDATE score_execution.jobs SET state=CASE action_name WHEN 'resume' THEN 'queued' WHEN 'pause' THEN 'paused' ELSE 'cancelled' END,generation=generation+1,owner_token=NULL,lease_until=NULL,finished_at=CASE WHEN action_name='cancel' THEN clock_timestamp() ELSE NULL END WHERE id=target RETURNING * INTO job;
 INSERT INTO design.audit_entries(id,actor_id,bid_id,event_type,payload,created_at) SELECT gen_random_uuid(),actor,i.bid_id,'score.job_'||action_name,jsonb_build_object('job_id',target,'generation',job.generation,'reason',reason_text),clock_timestamp() FROM score_execution.inputs i WHERE i.id=job.input_id;
 RETURN job.generation;
END $$;
CREATE FUNCTION score_execution.persist_shadow(target_job uuid,target_model uuid,target_key text,expected_revision bigint,document jsonb,actor uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_execution,model_release,model_governance,design,pg_temp AS $$
DECLARE job score_execution.jobs; i score_execution.inputs; model model_release.registrations; route model_release.routes; member record; result score_execution.results; identity uuid:=gen_random_uuid();
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' OR target_model IS NULL OR target_key IS NULL OR expected_revision IS NULL OR actor IS NULL OR document IS NULL THEN RAISE EXCEPTION 'shadow_command_invalid' USING ERRCODE='22023'; END IF;
 SELECT * INTO job FROM score_execution.jobs WHERE id=target_job;
 SELECT * INTO i FROM score_execution.inputs WHERE id=job.input_id;
 SELECT * INTO model FROM model_release.registrations WHERE model_id=target_model;
 IF job.id IS NULL OR i.id IS NULL OR model.model_id IS NULL THEN RAISE EXCEPTION 'shadow_input_missing' USING ERRCODE='P0002'; END IF;
 -- Union current input + complete training/group dependencies BEFORE any graph lock.
 PERFORM pg_advisory_xact_lock(91143,1);
 FOR member IN SELECT DISTINCT bid_id FROM (
  SELECT i.bid_id AS bid_id UNION SELECT m.bid_id FROM model_governance.members m WHERE m.cohort_id=model.cohort_id
  UNION SELECT s.bid_id FROM design.samples s WHERE s.source_group_id IN (SELECT m.group_id FROM model_governance.members m WHERE m.cohort_id=model.cohort_id)
 ) all_bids ORDER BY bid_id LOOP PERFORM design.lock_worker_bid(member.bid_id); END LOOP;
 IF NOT score_execution.current_input(i.id) OR NOT model_governance.current_cohort(model.cohort_id) THEN RAISE EXCEPTION 'shadow_dependencies_changed' USING ERRCODE='55000'; END IF;
 SELECT * INTO route FROM model_release.routes WHERE route_key=target_key AND lane='shadow';
 SELECT * INTO result FROM score_execution.results WHERE job_id=target_job;
 IF route.model_id IS DISTINCT FROM target_model OR route.revision IS DISTINCT FROM expected_revision OR result.job_id IS NULL
  OR model.document->>'judgment_method_digest' IS DISTINCT FROM i.method_digest::text THEN RAISE EXCEPTION 'shadow_route_changed' USING ERRCODE='40001'; END IF;
 -- Initial producer has no prebid total-maximum authority; it cannot generate a numeric
 -- score by summing rules or reading an official label. Persist explicit unavailability.
 IF NOT score_execution.exact_keys(document,ARRAY['schema','job_id','model_id','input_digest','score_result_digest','model_artifact_digest','method_digest','prediction','formal_eligibility']) OR NOT score_execution.exact_keys(document->'prediction',ARRAY['mode','value','intervals','reasons']) OR document->>'schema' IS DISTINCT FROM 'persisted-score-shadow/1' OR document->>'job_id' IS DISTINCT FROM target_job::text
  OR document->>'score_result_digest' IS DISTINCT FROM result.digest::text OR document->>'model_id' IS DISTINCT FROM target_model::text
  OR document->>'model_artifact_digest' IS DISTINCT FROM model.document_digest::text OR document->>'input_digest' IS DISTINCT FROM result.input_digest::text
  OR document->>'method_digest' IS DISTINCT FROM i.method_digest::text OR document->>'formal_eligibility' IS DISTINCT FROM 'unavailable'
  OR document->'prediction'->>'mode' IS DISTINCT FROM 'unavailable' OR document->'prediction'->'value' IS DISTINCT FROM 'null'::jsonb
  OR document->'prediction'->'intervals' IS DISTINCT FROM 'null'::jsonb THEN RAISE EXCEPTION 'shadow_payload_invalid' USING ERRCODE='23514'; END IF;
 INSERT INTO score_execution.shadows VALUES(identity,target_job,target_model,target_key,expected_revision,document,encode(sha256(convert_to(document::text,'UTF8')),'hex'),actor,clock_timestamp());
 RETURN identity;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA score_execution FROM PUBLIC;
GRANT EXECUTE ON FUNCTION score_execution.seal_input(uuid,uuid),score_execution.current_input(uuid),score_execution.start_job(uuid,numeric,uuid,uuid),score_execution.change_job(uuid,bigint,text,uuid,text),score_execution.persist_shadow(uuid,uuid,text,bigint,jsonb,uuid) TO tender_api;
GRANT EXECUTE ON FUNCTION score_execution.claim_job(uuid,bigint,uuid),score_execution.heartbeat(uuid,bigint,bigint,uuid),score_execution.owned_input(uuid,bigint,bigint,uuid),score_execution.reserve_call(uuid,bigint,bigint,uuid,uuid,integer,design.sha256,design.sha256,numeric),score_execution.settle_call(uuid,uuid,text,text,numeric),score_execution.success_response(uuid,bigint,bigint,uuid,uuid),score_execution.publish_result(uuid,bigint,bigint,uuid,jsonb),score_execution.pause_failed(uuid,bigint,bigint,uuid) TO tender_model_score;
-- No raw table grants, generic blob keys, model calls, labels, exports, proofs or CPU role.
-- No certification issuer or nonnull formal business route is granted by this execution authority.
COMMIT;
