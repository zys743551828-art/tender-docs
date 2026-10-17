-- Score denominator DRAFT C. Static review, then NEW fresh DB only.
-- Requires the exact Score F, Scale C and StageA D baselines; changes none of them.
BEGIN;
CREATE SCHEMA score_basis;
REVOKE ALL ON SCHEMA score_basis FROM PUBLIC;
GRANT USAGE ON SCHEMA score_basis TO tender_api;
CREATE TABLE score_basis.bindings(
 id uuid PRIMARY KEY,score_input_id uuid REFERENCES score_execution.input_seals(input_id),
 historical_input_id uuid REFERENCES historical_backtest.inputs(id),
 cohort_id uuid REFERENCES model_governance.cohorts(id),sample_id uuid REFERENCES design.samples(id),
 scale_version_id uuid REFERENCES technical_scale.versions(id),scale_decision_id uuid REFERENCES technical_scale.decisions(id),
 scale_head_revision bigint,scope jsonb NOT NULL,scope_digest design.sha256 NOT NULL,
 recorded_maximum numeric,scale_description text,recorded_status text NOT NULL CHECK(recorded_status IN ('usable','unavailable','conflict')),
 reason text NOT NULL,created_by uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((score_input_id IS NOT NULL)::int+(historical_input_id IS NOT NULL)::int+(cohort_id IS NOT NULL)::int=1),
 CHECK((cohort_id IS NULL)=(sample_id IS NULL)),
 CHECK((scale_version_id IS NULL)=(scale_decision_id IS NULL) AND (scale_version_id IS NULL)=(scale_head_revision IS NULL)),
 CHECK((recorded_status='usable')=(recorded_maximum IS NOT NULL)),
 CHECK(recorded_maximum>0 AND recorded_maximum<=1000000000 AND recorded_maximum NOT IN ('NaN','Infinity','-Infinity')),
 CHECK(jsonb_typeof(scope)='object'),
 UNIQUE NULLS NOT DISTINCT(score_input_id,historical_input_id,cohort_id,sample_id,scale_version_id)
);
CREATE TABLE score_basis.results(
 id uuid PRIMARY KEY,binding_id uuid NOT NULL REFERENCES score_basis.bindings(id),job_id uuid NOT NULL REFERENCES score_execution.results(job_id),
 result_digest design.sha256 NOT NULL,document jsonb NOT NULL,digest design.sha256 NOT NULL,
 created_by uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(binding_id,job_id),CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=8388608)
);
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON score_basis.bindings FOR EACH ROW EXECUTE FUNCTION design.append_only();
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON score_basis.results FOR EACH ROW EXECUTE FUNCTION design.append_only();
REVOKE ALL ON ALL TABLES IN SCHEMA score_basis FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_research_read,tender_score_read,tender_model_train,tender_model_evaluate,tender_model_score;
GRANT SELECT ON ALL TABLES IN SCHEMA score_basis TO tender_api;

-- Read the authoritative scope, never caller-supplied max/identity/rule lists.
-- Cohort qualification takes 91143 -> ALL sorted bids -> graph before any per-bid lock.
CREATE FUNCTION score_basis.target_scope(score_input uuid,historical uuid,cohort uuid,sample uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_basis,score_execution,historical_backtest,historical_input,model_governance,design,pg_temp AS $$
DECLARE bid uuid; review uuid; submission uuid; subject uuid; material uuid; ruleset uuid; method design.sha256;
 identity jsonb; boundary timestamptz; submitted_at timestamptz; purpose text; rules jsonb; total numeric;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'basis_read_committed_required' USING ERRCODE='55000'; END IF;
 IF (score_input IS NOT NULL)::int+(historical IS NOT NULL)::int+(cohort IS NOT NULL)::int<>1 OR (cohort IS NULL)<>(sample IS NULL) THEN RAISE EXCEPTION 'basis_scope_invalid' USING ERRCODE='22023'; END IF;
 IF cohort IS NOT NULL THEN
  IF NOT model_governance.current_cohort(cohort) THEN RAISE EXCEPTION 'basis_cohort_stale' USING ERRCODE='55000'; END IF;
  SELECT m.bid_id,m.submission_id,s.review_id,m.identity_snapshot,s.submitted_at INTO bid,submission,review,identity,submitted_at
   FROM model_governance.members m JOIN model_governance.cohorts c ON c.id=m.cohort_id JOIN design.submission_records s ON s.id=m.submission_id
   WHERE m.cohort_id=cohort AND m.sample_id=sample AND m.purpose<>'excluded';
  IF NOT FOUND OR review IS NULL THEN RAISE EXCEPTION 'basis_strict_member_missing' USING ERRCODE='55000'; END IF;
  SELECT i.material_snapshot_id,(i.manifest->>'rule_set_id')::uuid,i.method_digest,i.as_of INTO material,ruleset,method,boundary
   FROM design.review_runs r JOIN design.review_inputs i ON i.id=r.input_id WHERE r.id=review AND r.bid_id=bid;
  SELECT experiment.method_digest INTO method FROM model_governance.cohorts c JOIN design.research_runs run ON run.id=c.research_run_id JOIN design.experiments experiment ON experiment.id=run.experiment_id WHERE c.id=cohort;
  purpose:='strict_prebid';
 ELSIF score_input IS NOT NULL THEN
  IF NOT score_execution.current_input(score_input) THEN RAISE EXCEPTION 'basis_score_input_stale' USING ERRCODE='55000'; END IF;
  SELECT i.bid_id,i.review_id,i.historical_input_id,i.identity_snapshot,i.method_digest INTO bid,review,historical,identity,method
   FROM score_execution.inputs i JOIN score_execution.input_seals seal ON seal.input_id=i.id WHERE i.id=score_input;
  IF NOT FOUND THEN RAISE EXCEPTION 'basis_score_input_missing' USING ERRCODE='55000'; END IF;
  IF historical IS NULL THEN
   SELECT i.material_snapshot_id,(i.manifest->>'rule_set_id')::uuid,i.as_of INTO material,ruleset,boundary FROM design.review_runs r JOIN design.review_inputs i ON i.id=r.input_id WHERE r.id=review AND r.bid_id=bid;
   purpose:='current_prebid';
  END IF;
 END IF;
 IF historical IS NOT NULL THEN
  IF NOT historical_input.current_input(historical) THEN RAISE EXCEPTION 'basis_historical_input_stale' USING ERRCODE='55000'; END IF;
  SELECT h.bid_id,h.submission_id,h.subject_id,h.material_snapshot_id,h.rule_set_id,h.method_digest,h.historical_as_of,s.identity_snapshot
   INTO bid,submission,subject,material,ruleset,method,boundary,identity FROM historical_backtest.inputs h JOIN historical_backtest.subjects s ON s.id=h.subject_id WHERE h.id=historical;
  IF NOT FOUND THEN RAISE EXCEPTION 'basis_historical_input_missing' USING ERRCODE='55000'; END IF;
  purpose:='historical_backtest';
 END IF;
 IF bid IS NULL OR material IS NULL OR ruleset IS NULL OR boundary IS NULL OR method IS NULL OR identity IS NULL THEN RAISE EXCEPTION 'basis_scope_incomplete' USING ERRCODE='55000'; END IF;
 SELECT jsonb_agg(jsonb_build_object('id',r.id,'maximum',r.max_points::text,'bands',r.bands,'evidence_ids',r.evidence_ids,'provenance_status',r.provenance_status) ORDER BY r.id),sum(r.max_points)
  INTO rules,total FROM design.rule_items r JOIN design.rule_sets s ON s.id=r.rule_set_id AND s.bid_id=r.bid_id
  WHERE r.bid_id=bid AND r.rule_set_id=ruleset AND r.kind='scoring' AND s.material_id=material AND s.coverage='complete';
 IF rules IS NULL OR total IS NULL OR EXISTS(SELECT FROM design.rule_items r WHERE r.rule_set_id=ruleset AND r.kind='scoring' AND r.provenance_status<>'effective') THEN RAISE EXCEPTION 'basis_rule_scope_incomplete' USING ERRCODE='55000'; END IF;
 RETURN jsonb_build_object('bid_id',bid,'purpose',purpose,'review_id',review,'submission_id',submission,'subject_id',subject,
  'material_snapshot_id',material,'rule_set_id',ruleset,'method_digest',method,'identity_snapshot',identity,'as_of',boundary,'submitted_at',submitted_at,'rules',rules,'rule_maximum_sum',total::text);
END $$;

CREATE FUNCTION score_basis.scale_matches(scope jsonb,target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_basis,technical_scale,design,pg_temp AS $$
DECLARE v technical_scale.versions; boundary timestamptz;
BEGIN
 IF scope IS NULL OR target IS NULL THEN RETURN false; END IF;
 SELECT * INTO v FROM technical_scale.versions WHERE id=target;
 IF NOT FOUND OR v.bid_id IS DISTINCT FROM (scope->>'bid_id')::uuid
  OR v.material_snapshot_id IS DISTINCT FROM (scope->>'material_snapshot_id')::uuid
  OR v.identity_snapshot IS DISTINCT FROM scope->'identity_snapshot' OR v.scale_description<>'未加权原始分' THEN RETURN false; END IF;
 IF NOT technical_scale.current_version(target) THEN RETURN false; END IF;
 boundary:=(scope->>'as_of')::timestamptz;
 IF scope->>'purpose'='historical_backtest' THEN
  RETURN v.purpose='historical_backtest' AND v.submission_id IS NOT DISTINCT FROM (scope->>'submission_id')::uuid AND v.subject_id IS NOT DISTINCT FROM (scope->>'subject_id')::uuid;
 END IF;
 -- A late human confirmation can enrich a still-current review without rerunning
 -- model judgments, but its exact source bytes must really have been prebid-visible.
 -- Strict samples additionally require the authority actually existed by submission.
 RETURN v.purpose='current_prebid'
  AND (scope->>'purpose'<>'strict_prebid' OR (v.created_at<=(scope->>'submitted_at')::timestamptz
   AND EXISTS(SELECT FROM technical_scale.heads h JOIN technical_scale.decisions d ON d.id=h.decision_id
    WHERE h.version_id=v.id AND d.version_id=v.id AND d.state='confirmed' AND d.created_at<=(scope->>'submitted_at')::timestamptz)))
  AND NOT EXISTS(SELECT FROM technical_scale.evidence e JOIN technical_scale.source_heads frozen ON frozen.evidence_id=e.id
   WHERE e.version_id=v.id AND (frozen.known_at>boundary OR frozen.valid_from>boundary OR frozen.valid_until<=boundary));
END $$;

CREATE FUNCTION score_basis.freeze(score_input uuid,historical uuid,cohort uuid,sample uuid,scale_version uuid,actor uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_basis,technical_scale,design,pg_temp AS $$
DECLARE scope jsonb; v technical_scale.versions; head technical_scale.heads; identity uuid:=gen_random_uuid(); status text:='unavailable'; maximum numeric; reason text:='technical_maximum_authority_unavailable'; old uuid;
BEGIN
 IF actor IS NULL THEN RAISE EXCEPTION 'basis_actor_required' USING ERRCODE='22023'; END IF;
 scope:=score_basis.target_scope(score_input,historical,cohort,sample);
 IF scale_version IS NOT NULL AND score_basis.scale_matches(scope,scale_version) THEN
  SELECT * INTO v FROM technical_scale.versions WHERE id=scale_version;
  SELECT * INTO head FROM technical_scale.heads WHERE version_id=scale_version;
  IF (scope->>'rule_maximum_sum')::numeric>v.technical_maximum THEN status:='conflict'; reason:='rule_maximum_sum_exceeds_sourced_total';
  ELSE status:='usable'; maximum:=v.technical_maximum; reason:='sourced_same_scale_denominator'; END IF;
 END IF;
 -- Invalid or withdrawn choices are not retained as a trusted decision capability.
 IF head.version_id IS NULL THEN scale_version:=NULL; END IF;
 SELECT id INTO old FROM score_basis.bindings b WHERE b.score_input_id IS NOT DISTINCT FROM score_input AND b.historical_input_id IS NOT DISTINCT FROM historical
  AND b.cohort_id IS NOT DISTINCT FROM cohort AND b.sample_id IS NOT DISTINCT FROM sample AND b.scale_version_id IS NOT DISTINCT FROM scale_version;
 IF FOUND THEN RETURN old; END IF;
 INSERT INTO score_basis.bindings(id,score_input_id,historical_input_id,cohort_id,sample_id,scale_version_id,scale_decision_id,scale_head_revision,scope,scope_digest,recorded_maximum,scale_description,recorded_status,reason,created_by)
 VALUES(identity,score_input,historical,cohort,sample,scale_version,head.decision_id,head.revision,scope,encode(sha256(convert_to(scope::text,'UTF8')),'hex'),maximum,v.scale_description,status,reason,actor);
 RETURN identity;
END $$;

CREATE FUNCTION score_basis.current_binding(target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_basis,technical_scale,pg_temp AS $$
DECLARE b score_basis.bindings; scope jsonb;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'basis_read_committed_required' USING ERRCODE='55000'; END IF;
 SELECT * INTO b FROM score_basis.bindings WHERE id=target;
 IF NOT FOUND THEN RETURN false; END IF;
 BEGIN
  scope:=score_basis.target_scope(b.score_input_id,b.historical_input_id,b.cohort_id,b.sample_id);
 EXCEPTION WHEN SQLSTATE '55000' OR SQLSTATE 'P0002' THEN RETURN false; END;
 RETURN b.recorded_status='usable' AND scope=b.scope AND score_basis.scale_matches(scope,b.scale_version_id)
  AND EXISTS(SELECT FROM technical_scale.heads h JOIN technical_scale.versions v ON v.id=h.version_id WHERE h.version_id=b.scale_version_id AND h.decision_id=b.scale_decision_id AND h.revision=b.scale_head_revision AND v.technical_maximum=b.recorded_maximum AND v.scale_description=b.scale_description);
END $$;

-- Derived feature artifacts reference the original complete three-judgment result;
-- they do not update it, create calls, or authorize a new execution/retry.
CREATE FUNCTION score_basis.publish_result(binding uuid,job uuid,payload jsonb,payload_digest design.sha256,actor uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,score_basis,score_execution,pg_temp AS $$
DECLARE b score_basis.bindings; original score_execution.results; input score_execution.inputs; identity uuid:=gen_random_uuid(); old uuid;
BEGIN
 IF binding IS NULL OR job IS NULL OR actor IS NULL OR payload IS NULL OR payload_digest IS NULL THEN RAISE EXCEPTION 'basis_result_missing' USING ERRCODE='22023'; END IF;
 SELECT * INTO b FROM score_basis.bindings WHERE id=binding;
 IF NOT FOUND OR NOT score_basis.current_binding(binding) THEN RAISE EXCEPTION 'basis_result_stale' USING ERRCODE='55000'; END IF;
 SELECT r.* INTO original FROM score_execution.results r JOIN score_execution.jobs j ON j.id=r.job_id WHERE r.job_id=job AND j.state='succeeded';
 SELECT i.* INTO input FROM score_execution.inputs i JOIN score_execution.jobs j ON j.input_id=i.id WHERE j.id=job;
 IF original.job_id IS NULL OR (b.score_input_id IS DISTINCT FROM input.id AND (b.historical_input_id IS NULL OR b.historical_input_id IS DISTINCT FROM input.historical_input_id))
  OR payload->>'schema' IS DISTINCT FROM 'sourced-score-coverage/1' OR payload->>'binding_id' IS DISTINCT FROM binding::text
  OR payload->>'source_result_digest' IS DISTINCT FROM original.digest::text OR payload->'items' IS DISTINCT FROM original.document->'items'
  OR payload->>'method_digest' IS DISTINCT FROM input.method_digest::text OR payload->'formal_eligibility' IS DISTINCT FROM '"unavailable"'::jsonb
  OR payload->'prediction' IS DISTINCT FROM '{"mode":"unavailable","value":null,"intervals":null}'::jsonb
  OR payload->>'technical_maximum' IS DISTINCT FROM b.recorded_maximum::text
  OR encode(sha256(convert_to(payload::text,'UTF8')),'hex') IS DISTINCT FROM payload_digest::text
 THEN RAISE EXCEPTION 'basis_result_binding_invalid' USING ERRCODE='23514'; END IF;
 SELECT id INTO old FROM score_basis.results WHERE binding_id=binding AND job_id=job;
 IF FOUND THEN
  IF NOT EXISTS(SELECT FROM score_basis.results WHERE id=old AND digest=payload_digest AND document=payload) THEN RAISE EXCEPTION 'basis_result_changed' USING ERRCODE='23514'; END IF;
  RETURN old;
 END IF;
 INSERT INTO score_basis.results VALUES(identity,binding,job,original.digest,payload,payload_digest,actor,clock_timestamp());
 RETURN identity;
END $$;

-- Only API can freeze or inspect a reference. Score/train/evaluate roles retain no
-- generic source/label authority or max writer; purpose capabilities are a later slice.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA score_basis FROM PUBLIC;
GRANT EXECUTE ON FUNCTION score_basis.freeze(uuid,uuid,uuid,uuid,uuid,uuid),score_basis.current_binding(uuid),score_basis.publish_result(uuid,uuid,jsonb,design.sha256,uuid) TO tender_api;
COMMIT;
