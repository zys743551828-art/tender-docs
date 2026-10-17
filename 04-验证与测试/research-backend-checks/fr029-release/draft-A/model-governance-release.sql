-- FR029 registry/routes DRAFT A. Static review then a NEW complete DB only.
-- Requires model-governance.sql D. Does not grant CPU/score roles raw model or label reads.
BEGIN;
CREATE SCHEMA model_release;
REVOKE ALL ON SCHEMA model_release FROM PUBLIC;
GRANT USAGE ON SCHEMA model_release TO tender_api;
CREATE TABLE model_release.registrations(
 model_id uuid PRIMARY KEY REFERENCES design.model_artifacts(id),
 training_job_id uuid NOT NULL UNIQUE REFERENCES model_governance.results(job_id),
 cohort_id uuid NOT NULL REFERENCES model_governance.cohorts(id),
 document_digest design.sha256 NOT NULL,document jsonb NOT NULL,
 actor_id uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=2097152)
);
-- No business LOGIN is granted INSERT/UPDATE/DELETE on certifications. Current baseline has
-- no function capable of minting one. Real provenance/independent acceptance/observation must
-- be implemented and reviewed as a separate authority, never inferred from synthetic metrics.
CREATE TABLE model_release.certifications(
 id uuid PRIMARY KEY,model_id uuid NOT NULL REFERENCES model_release.registrations(model_id),
 acceptance_id uuid NOT NULL REFERENCES design.model_acceptances(id),
 scope_digest design.sha256 NOT NULL,report_digest design.sha256 NOT NULL,
 protocol_digest design.sha256 NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(model_id,id)
);
CREATE TABLE model_release.routes(
 route_key text NOT NULL CHECK(length(route_key) BETWEEN 1 AND 128),
 lane text NOT NULL CHECK(lane IN ('shadow','business')),
 revision bigint NOT NULL CHECK(revision>0),model_id uuid REFERENCES model_release.registrations(model_id),
 certification_id uuid,rollback_model_id uuid REFERENCES model_release.registrations(model_id),
 PRIMARY KEY(route_key,lane),FOREIGN KEY(model_id,certification_id) REFERENCES model_release.certifications(model_id,id),
 CHECK(lane='business' OR certification_id IS NULL),
 CHECK(lane<>'business' OR (model_id IS NULL)=(certification_id IS NULL))
);
CREATE TABLE model_release.route_events(
 id uuid PRIMARY KEY,route_key text NOT NULL,lane text NOT NULL,revision bigint NOT NULL CHECK(revision>0),
 model_id uuid REFERENCES model_release.registrations(model_id),certification_id uuid REFERENCES model_release.certifications(id),
 previous_model_id uuid REFERENCES model_release.registrations(model_id),
 action text NOT NULL CHECK(action IN ('publish','rollback','suspend')),
 actor_id uuid NOT NULL REFERENCES design.members(id),reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 4000),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(route_key,lane,revision)
);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['registrations','certifications','route_events'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON model_release.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',tab);
 END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA model_release FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_research_read,tender_score_read,tender_model_train,tender_model_evaluate,tender_model_score;
GRANT SELECT ON model_release.registrations,model_release.routes,model_release.route_events TO tender_api;

-- The UNION scope is acquired BEFORE calling current_cohort (which takes the graph lock).
-- Locking candidate, current and rollback cohorts one-by-one could deadlock identity writers.
CREATE FUNCTION model_release.lock_scope(targets uuid[]) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_release,model_governance,design,pg_temp AS $$
DECLARE item record;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'read_committed_required' USING ERRCODE='55000'; END IF;
 PERFORM pg_advisory_xact_lock(91143,1);
 FOR item IN SELECT DISTINCT all_bids.bid_id FROM (
  SELECT member.bid_id FROM model_governance.members member JOIN model_release.registrations r ON r.cohort_id=member.cohort_id WHERE r.model_id=ANY(targets)
  UNION SELECT sample.bid_id FROM design.samples sample WHERE sample.source_group_id IN (
   SELECT member.group_id FROM model_governance.members member JOIN model_release.registrations r ON r.cohort_id=member.cohort_id WHERE r.model_id=ANY(targets))
 ) all_bids ORDER BY all_bids.bid_id LOOP PERFORM design.lock_worker_bid(item.bid_id); END LOOP;
END $$;
CREATE FUNCTION model_release.registration_document(target_job uuid,target_model uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_release,model_governance,design,pg_temp AS $$
DECLARE job model_governance.jobs; result model_governance.results; cohort model_governance.cohorts;
 protocol model_governance.protocols; method design.sha256; scope jsonb;
BEGIN
 SELECT * INTO job FROM model_governance.jobs WHERE id=target_job;
 IF NOT FOUND OR job.kind<>'train' OR NOT model_governance.current_cohort(job.cohort_id) THEN RAISE EXCEPTION 'training_result_unavailable' USING ERRCODE='55000'; END IF;
 SELECT * INTO job FROM model_governance.jobs WHERE id=target_job FOR UPDATE;
 IF job.kind<>'train' OR job.state<>'succeeded' THEN RAISE EXCEPTION 'training_result_unavailable' USING ERRCODE='55000'; END IF;
 SELECT * INTO result FROM model_governance.results WHERE job_id=target_job;
 SELECT * INTO cohort FROM model_governance.cohorts WHERE id=job.cohort_id;
 SELECT * INTO protocol FROM model_governance.protocols WHERE id=cohort.protocol_id;
 SELECT e.method_digest INTO method FROM design.research_runs r JOIN design.experiments e ON e.id=r.experiment_id WHERE r.id=cohort.research_run_id;
 IF result.job_id IS NULL OR result.payload->>'schema' IS DISTINCT FROM 'frozen-training-result/1'
  OR result.payload->>'origin' IS DISTINCT FROM cohort.origin OR result.payload->>'formal_eligibility' IS DISTINCT FROM 'unavailable'
  OR result.payload->>'protocol_digest' IS DISTINCT FROM protocol.digest::text OR method IS NULL THEN
  RAISE EXCEPTION 'training_identity_invalid' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('judgment_method_digest',method,'feature_schema','complete-three-band-features/1',
  'engineering_types',coalesce(jsonb_agg(DISTINCT row->'engineering_type' ORDER BY row->'engineering_type'),'[]'::jsonb),
  'maximums',coalesce(jsonb_agg(DISTINCT row->'maximum' ORDER BY row->'maximum'),'[]'::jsonb)) INTO scope
 FROM model_governance.exports export CROSS JOIN LATERAL jsonb_array_elements(export.payload->'rows') row
 WHERE export.cohort_id=cohort.id AND export.purpose IN ('training','final_features');
 RETURN jsonb_build_object('schema','registered-score-artifact/1','model_id',target_model,'training_job_id',target_job,
  'training_result_digest',result.digest,'cohort_digest',cohort.digest,'protocol_digest',protocol.digest,
  'judgment_method_digest',method,'feature_schema','complete-three-band-features/1','origin',cohort.origin,
  'research_only',cohort.origin='synthetic','formal_eligibility','unavailable','scope',scope,
  'model',result.payload->'model','model_digest',result.payload->'model_digest','intervals',result.payload->'intervals','baselines',result.payload->'baselines');
END $$;
CREATE FUNCTION model_release.register_artifact(target_model uuid,target_job uuid,target_blob uuid,raw_document text,actor uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_release,model_governance,design,pg_temp AS $$
DECLARE expected jsonb; blob design.blobs; job model_governance.jobs; raw_digest text;
BEGIN
 IF octet_length(raw_document)>2097152 THEN RAISE EXCEPTION 'artifact_too_large' USING ERRCODE='22023'; END IF;
 expected:=model_release.registration_document(target_job,target_model);
 IF raw_document::jsonb IS DISTINCT FROM expected THEN RAISE EXCEPTION 'artifact_identity_mismatch' USING ERRCODE='23514'; END IF;
 SELECT * INTO blob FROM design.blobs WHERE id=target_blob;
 raw_digest:=encode(sha256(convert_to(raw_document,'UTF8')),'hex');
 IF blob.id IS NULL OR blob.digest::text<>raw_digest OR blob.byte_size<>octet_length(raw_document)
  OR blob.media_type<>'application/vnd.tender.score-model+json' THEN RAISE EXCEPTION 'artifact_blob_mismatch' USING ERRCODE='23514'; END IF;
 SELECT * INTO job FROM model_governance.jobs WHERE id=target_job;
 INSERT INTO design.model_artifacts(id,artifact_blob_id,method_digest,scope,training_manifest_digest)
 VALUES(target_model,target_blob,(expected->>'judgment_method_digest')::design.sha256,expected->'scope',(expected->>'cohort_digest')::design.sha256);
 INSERT INTO model_governance.model_records(model_id,training_job_id,origin,model_digest)
 VALUES(target_model,target_job,expected->>'origin',(expected->>'model_digest')::design.sha256);
 INSERT INTO model_release.registrations(model_id,training_job_id,cohort_id,document_digest,document,actor_id)
 VALUES(target_model,target_job,job.cohort_id,raw_digest,expected,actor);
 RETURN target_model;
END $$;
CREATE FUNCTION model_release.change_route(target_key text,target_lane text,target_model uuid,expected_revision bigint,target_certification uuid,action_name text,actor uuid,reason_text text,event_id uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_release,model_governance,design,pg_temp AS $$
DECLARE old model_release.routes; candidate model_release.registrations; certificate model_release.certifications;
 accepted design.model_acceptances; next_revision bigint; scope_digest text;
BEGIN
 IF target_lane NOT IN ('shadow','business') OR action_name NOT IN ('publish','rollback','suspend') OR expected_revision<0
  OR length(btrim(reason_text)) NOT BETWEEN 1 AND 4000 OR length(target_key) NOT BETWEEN 1 AND 128 THEN RAISE EXCEPTION 'route_command_invalid' USING ERRCODE='22023'; END IF;
 -- 91143 serializes all release changes and source-group writers; current route cannot change
 -- before this read. Ordinary identity writers are serialized by sorted bid locks next.
 PERFORM pg_advisory_xact_lock(91143,1);
 SELECT * INTO old FROM model_release.routes WHERE route_key=target_key AND lane=target_lane;
 IF coalesce(old.revision,0)<>expected_revision THEN RAISE EXCEPTION 'route_revision_conflict' USING ERRCODE='40001'; END IF;
 IF action_name='suspend' AND target_model IS NOT NULL THEN RAISE EXCEPTION 'suspend_requires_null' USING ERRCODE='22023'; END IF;
 IF action_name='rollback' AND target_model IS DISTINCT FROM old.rollback_model_id THEN RAISE EXCEPTION 'rollback_target_mismatch' USING ERRCODE='23514'; END IF;
 PERFORM model_release.lock_scope(ARRAY[target_model,old.model_id,old.rollback_model_id]);
 IF target_model IS NOT NULL THEN
  SELECT * INTO candidate FROM model_release.registrations WHERE model_id=target_model;
  IF candidate.model_id IS NULL OR NOT model_governance.current_cohort(candidate.cohort_id) THEN RAISE EXCEPTION 'model_source_stale' USING ERRCODE='55000'; END IF;
  IF target_key<>encode(sha256(convert_to((candidate.document->'scope')::text,'UTF8')),'hex') THEN RAISE EXCEPTION 'route_scope_mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 IF target_lane='shadow' AND target_certification IS NOT NULL THEN RAISE EXCEPTION 'shadow_not_certified_business' USING ERRCODE='23514'; END IF;
 IF target_lane='business' AND target_model IS NOT NULL THEN
  SELECT * INTO certificate FROM model_release.certifications WHERE id=target_certification AND model_id=target_model;
  SELECT * INTO accepted FROM design.model_acceptances WHERE id=certificate.acceptance_id AND model_id=target_model AND decision='accepted';
  scope_digest:=encode(sha256(convert_to((candidate.document->'scope')::text,'UTF8')),'hex');
  IF certificate.id IS NULL OR accepted.id IS NULL OR candidate.document->>'origin'<>'real'
   OR certificate.scope_digest::text<>scope_digest THEN RAISE EXCEPTION 'formal_model_unavailable' USING ERRCODE='55000'; END IF;
 END IF;
 IF target_model IS NULL AND target_certification IS NOT NULL THEN RAISE EXCEPTION 'null_route_not_certified' USING ERRCODE='23514'; END IF;
 next_revision:=expected_revision+1;
 INSERT INTO model_release.routes(route_key,lane,revision,model_id,certification_id,rollback_model_id)
 VALUES(target_key,target_lane,next_revision,target_model,target_certification,CASE WHEN action_name='publish' THEN old.model_id ELSE NULL END)
 ON CONFLICT(route_key,lane) DO UPDATE SET revision=excluded.revision,model_id=excluded.model_id,certification_id=excluded.certification_id,rollback_model_id=excluded.rollback_model_id;
 INSERT INTO model_release.route_events(id,route_key,lane,revision,model_id,certification_id,previous_model_id,action,actor_id,reason)
 VALUES(event_id,target_key,target_lane,next_revision,target_model,target_certification,old.model_id,action_name,actor,reason_text);
 -- Business null suspension is usable now; non-null publication requires the separate real
 -- certification authority, absent in this baseline. Research shadow never touches this table.
 IF target_lane='business' THEN
  INSERT INTO design.model_routes(route_key,revision,model_id,acceptance_id,rollback_model_id)
  VALUES(target_key,next_revision,target_model,certificate.acceptance_id,CASE WHEN action_name='publish' THEN old.model_id ELSE NULL END)
  ON CONFLICT(route_key) DO UPDATE SET revision=excluded.revision,model_id=excluded.model_id,acceptance_id=excluded.acceptance_id,rollback_model_id=excluded.rollback_model_id;
 END IF;
 RETURN next_revision;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA model_release FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_release.registration_document(uuid,uuid),model_release.register_artifact(uuid,uuid,uuid,text,uuid),model_release.change_route(text,text,uuid,bigint,uuid,text,uuid,text,uuid) TO tender_api;
COMMIT;
