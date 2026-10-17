-- Historical proof DRAFT C: NEW DB after architecture review only. Never backdate source.
-- This draft implements proof/subject authority and a private historical-input mapping.
-- No research/score worker execute/read capability is granted; owned-job integration is later.
BEGIN;
CREATE SCHEMA historical_backtest;
REVOKE ALL ON SCHEMA historical_backtest FROM PUBLIC;
GRANT USAGE ON SCHEMA historical_backtest TO tender_api;
CREATE TABLE historical_backtest.subjects(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,submission_id uuid NOT NULL,
 body_version_id uuid NOT NULL,body_digest design.sha256 NOT NULL,submitted_at timestamptz NOT NULL,
 bidder_id uuid REFERENCES design.bidders(id),round_id uuid REFERENCES design.bid_rounds(id),
 engineering_project_id uuid REFERENCES design.engineering_projects(id),identity_snapshot jsonb,
 declared_origin text NOT NULL CHECK(declared_origin IN ('real','synthetic','uncertain')),
 supersedes_id uuid REFERENCES historical_backtest.subjects(id),reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 4000),
 actor_id uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,submission_id) REFERENCES design.submission_records(bid_id,id),
 FOREIGN KEY(bid_id,body_version_id) REFERENCES design.document_versions(bid_id,id),UNIQUE(bid_id,id),
 CHECK((bidder_id IS NULL AND round_id IS NULL AND engineering_project_id IS NULL AND identity_snapshot IS NULL)
  OR coalesce(bidder_id IS NOT NULL AND round_id IS NOT NULL AND engineering_project_id IS NOT NULL
   AND jsonb_typeof(identity_snapshot)='object'
   AND identity_snapshot ?& ARRAY['bidder_id','bidder_name','round_id','engineering_project_id','engineering_title','region','engineering_type','lot','round_no']
   AND length(btrim(identity_snapshot->>'bidder_name'))>0 AND length(btrim(identity_snapshot->>'engineering_title'))>0
   AND identity_snapshot->>'bidder_id'=bidder_id::text AND identity_snapshot->>'round_id'=round_id::text
   AND identity_snapshot->>'engineering_project_id'=engineering_project_id::text,false))
);
CREATE TABLE historical_backtest.claims(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,submission_id uuid NOT NULL,document_version_id uuid NOT NULL,
 document_digest design.sha256 NOT NULL,subject_id uuid REFERENCES historical_backtest.subjects(id),
 declared_origin text NOT NULL CHECK(declared_origin IN ('real','synthetic','uncertain')),
 supersedes_id uuid REFERENCES historical_backtest.claims(id),reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 4000),
 actor_id uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,submission_id) REFERENCES design.submission_records(bid_id,id),
 FOREIGN KEY(bid_id,document_version_id) REFERENCES design.document_versions(bid_id,id),UNIQUE(bid_id,id)
);
CREATE TABLE historical_backtest.facts(
 claim_id uuid NOT NULL REFERENCES historical_backtest.claims(id),
 fact_kind text NOT NULL CHECK(fact_kind IN ('content_existed','published','effective','known_to_bidder','received_by_bidder')),
 claimed_at timestamptz,valid_until timestamptz,explanation text NOT NULL CHECK(length(btrim(explanation)) BETWEEN 1 AND 4000),
 PRIMARY KEY(claim_id,fact_kind),CHECK(valid_until IS NULL OR (fact_kind='effective' AND claimed_at IS NOT NULL AND valid_until>claimed_at))
);
-- Proof data is audit-only, never a model content source. API checks canonical source IDs
-- with the shared helper, SQL checks typed document/parse/block/parent relationships again.
CREATE TABLE historical_backtest.proofs(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,subject_id uuid,claim_id uuid,fact_kind text,
 document_version_id uuid NOT NULL,document_digest design.sha256 NOT NULL,parse_id uuid NOT NULL,
 evidence_id uuid NOT NULL,physical_page integer NOT NULL CHECK(physical_page>0),evidence_digest design.sha256 NOT NULL,
 source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),root_source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),
 quote text NOT NULL CHECK(length(quote) BETWEEN 1 AND 16000),explanation text NOT NULL CHECK(length(btrim(explanation)) BETWEEN 1 AND 4000),
 FOREIGN KEY(bid_id,subject_id) REFERENCES historical_backtest.subjects(bid_id,id),
 FOREIGN KEY(bid_id,claim_id) REFERENCES historical_backtest.claims(bid_id,id),
 FOREIGN KEY(claim_id,fact_kind) REFERENCES historical_backtest.facts(claim_id,fact_kind),
 FOREIGN KEY(bid_id,document_version_id) REFERENCES design.document_versions(bid_id,id),
 FOREIGN KEY(bid_id,parse_id,document_version_id) REFERENCES design.parse_runs(bid_id,id,document_version_id),
 FOREIGN KEY(bid_id,evidence_id) REFERENCES design.evidence_blocks(bid_id,id),
 CHECK((subject_id IS NOT NULL AND claim_id IS NULL AND fact_kind IS NULL) OR (subject_id IS NULL AND claim_id IS NOT NULL AND fact_kind IS NOT NULL))
);
CREATE TABLE historical_backtest.proof_heads(
 proof_id uuid NOT NULL REFERENCES historical_backtest.proofs(id),ancestor_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),
 revision bigint NOT NULL,digest design.sha256 NOT NULL,policy_class text NOT NULL,known_at timestamptz NOT NULL,
 valid_from timestamptz NOT NULL,valid_until timestamptz,PRIMARY KEY(proof_id,ancestor_id)
);
CREATE TABLE historical_backtest.decisions(
 id uuid PRIMARY KEY,object_kind text NOT NULL CHECK(object_kind IN ('subject','claim')),object_id uuid NOT NULL,
 decision text NOT NULL CHECK(decision IN ('pending','confirmed','rejected')),actor_id uuid NOT NULL REFERENCES design.members(id),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 4000),created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE historical_backtest.subject_heads(
 submission_id uuid PRIMARY KEY REFERENCES design.submission_records(id),subject_id uuid NOT NULL REFERENCES historical_backtest.subjects(id),
 decision_id uuid NOT NULL REFERENCES historical_backtest.decisions(id),revision bigint NOT NULL CHECK(revision>0)
);
CREATE TABLE historical_backtest.claim_heads(
 submission_id uuid NOT NULL REFERENCES design.submission_records(id),document_version_id uuid NOT NULL REFERENCES design.document_versions(id),
 claim_id uuid NOT NULL REFERENCES historical_backtest.claims(id),decision_id uuid NOT NULL REFERENCES historical_backtest.decisions(id),revision bigint NOT NULL CHECK(revision>0),
 PRIMARY KEY(submission_id,document_version_id)
);
CREATE TABLE historical_backtest.inputs(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,submission_id uuid NOT NULL,body_version_id uuid NOT NULL,body_digest design.sha256 NOT NULL,
 subject_id uuid NOT NULL REFERENCES historical_backtest.subjects(id),subject_decision_id uuid NOT NULL REFERENCES historical_backtest.decisions(id),
 historical_as_of timestamptz NOT NULL,rule_set_id uuid NOT NULL,material_snapshot_id uuid NOT NULL,
 method_digest design.sha256 NOT NULL,manifest_blob_id uuid NOT NULL REFERENCES design.blobs(id),manifest_digest design.sha256 NOT NULL,
 origin text NOT NULL CHECK(origin IN ('real','synthetic','uncertain')),
 prediction_timing text NOT NULL DEFAULT 'retrospective' CHECK(prediction_timing='retrospective'),
 execution_purpose text NOT NULL DEFAULT 'historical_backtest' CHECK(execution_purpose='historical_backtest'),
 research_only boolean NOT NULL DEFAULT true CHECK(research_only),
 actor_id uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,submission_id) REFERENCES design.submission_records(bid_id,id),
 FOREIGN KEY(bid_id,body_version_id) REFERENCES design.document_versions(bid_id,id),
 FOREIGN KEY(bid_id,rule_set_id) REFERENCES design.rule_sets(bid_id,id),
 FOREIGN KEY(bid_id,material_snapshot_id) REFERENCES design.material_snapshots(bid_id,id)
);
CREATE TABLE historical_backtest.input_claims(
 input_id uuid NOT NULL REFERENCES historical_backtest.inputs(id),claim_id uuid NOT NULL REFERENCES historical_backtest.claims(id),
 decision_id uuid NOT NULL REFERENCES historical_backtest.decisions(id),
 purpose text NOT NULL CHECK(purpose IN ('body','tender','supplement','prebid_fact')),PRIMARY KEY(input_id,claim_id)
);
CREATE TABLE historical_backtest.input_parses(
 input_id uuid NOT NULL REFERENCES historical_backtest.inputs(id),document_version_id uuid NOT NULL REFERENCES design.document_versions(id),
 parse_id uuid NOT NULL REFERENCES design.parse_runs(id),source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),root_source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),
 PRIMARY KEY(input_id,document_version_id)
);
CREATE TABLE historical_backtest.input_source_heads(
 input_id uuid NOT NULL REFERENCES historical_backtest.inputs(id),source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),
 ancestor_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),revision bigint NOT NULL,digest design.sha256 NOT NULL,
 known_at timestamptz NOT NULL,valid_from timestamptz NOT NULL,valid_until timestamptz,PRIMARY KEY(input_id,source_id,ancestor_id)
);
CREATE TABLE historical_backtest.input_evidence(
 input_id uuid NOT NULL REFERENCES historical_backtest.inputs(id),evidence_id uuid NOT NULL REFERENCES design.evidence_blocks(id),
 parse_id uuid NOT NULL REFERENCES design.parse_runs(id),physical_page integer NOT NULL,evidence_digest design.sha256 NOT NULL,
 PRIMARY KEY(input_id,evidence_id)
);
-- No fake review_id FK. Complete historical chapter authority is bound to this input and
-- exact current rule/parse evidence, with all necessary rules checked before execution.
CREATE TABLE historical_backtest.chapters(
 id uuid PRIMARY KEY,input_id uuid NOT NULL REFERENCES historical_backtest.inputs(id),rule_id uuid NOT NULL REFERENCES design.rule_items(id),
 body_parse_id uuid NOT NULL REFERENCES design.parse_runs(id),manifest_blob_id uuid NOT NULL REFERENCES design.blobs(id),digest design.sha256 NOT NULL,
 UNIQUE(input_id,rule_id)
);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['subjects','claims','facts','proofs','proof_heads','decisions','inputs','input_claims','input_parses','input_source_heads','input_evidence','chapters'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON historical_backtest.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',tab);
 END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA historical_backtest FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_research_read,tender_score_read,tender_model_train,tender_model_evaluate,tender_model_score;
-- API creates immutable claims and private frozen mappings, never directly sets confirmed heads.
GRANT SELECT ON ALL TABLES IN SCHEMA historical_backtest TO tender_api;
GRANT INSERT ON historical_backtest.subjects,historical_backtest.claims,historical_backtest.facts,historical_backtest.proofs,historical_backtest.proof_heads,historical_backtest.inputs,historical_backtest.input_claims,historical_backtest.input_parses,historical_backtest.input_source_heads,historical_backtest.input_evidence,historical_backtest.chapters TO tender_api;

CREATE FUNCTION historical_backtest.lock_submission(target uuid) RETURNS design.submission_records
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,historical_backtest,design,pg_temp AS $$
DECLARE record design.submission_records;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'read_committed_required' USING ERRCODE='55000'; END IF;
 PERFORM pg_advisory_xact_lock(91143,1);
 SELECT * INTO record FROM design.submission_records WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'submission_missing' USING ERRCODE='P0002'; END IF;
 PERFORM design.lock_worker_bid(record.bid_id);
 PERFORM pg_advisory_xact_lock(90127,1);
 SELECT * INTO record FROM design.submission_records WHERE id=target;
 IF NOT EXISTS(SELECT FROM design.submission_heads h WHERE h.submission_id=record.id AND h.bid_id=record.bid_id) THEN RAISE EXCEPTION 'submission_stale' USING ERRCODE='55000'; END IF;
 RETURN record;
END $$;
CREATE FUNCTION historical_backtest.current_proofs(target_subject uuid,target_claim uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,historical_backtest,design,retrieval,pg_temp AS $$
DECLARE proof historical_backtest.proofs; total integer:=0;
BEGIN
 -- Call only after lock_submission. This private helper grants no generic source oracle.
 FOR proof IN SELECT * FROM historical_backtest.proofs WHERE (subject_id=target_subject AND target_claim IS NULL) OR (claim_id=target_claim AND target_subject IS NULL) LOOP
  total:=total+1;
  IF NOT EXISTS(SELECT FROM design.evidence_blocks e JOIN design.parse_runs p ON p.id=e.parse_id JOIN design.pages page ON page.parse_id=p.id AND page.physical_page=e.physical_page
   JOIN design.document_versions d ON d.id=p.document_version_id JOIN design.blobs b ON b.id=d.blob_id
   JOIN retrieval.source_nodes n ON n.id=proof.source_id JOIN retrieval.source_nodes root ON root.id=proof.root_source_id
   WHERE e.id=proof.evidence_id AND e.bid_id=proof.bid_id AND e.parse_id=proof.parse_id AND e.physical_page=proof.physical_page AND e.digest=proof.evidence_digest
    AND position(proof.quote IN e.text_content)>0 AND p.document_version_id=proof.document_version_id AND b.digest=proof.document_digest
    AND p.id=(SELECT latest.id FROM design.parse_runs latest WHERE latest.document_version_id=d.id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
    AND p.coverage='complete' AND p.gaps='[]'::jsonb AND page.quality='usable' AND page.gaps='[]'::jsonb
    AND n.document_version_id=d.id AND root.document_version_id=d.id AND n.kind='document' AND root.kind='document' AND n.digest=b.digest AND root.digest=b.digest
    AND EXISTS(SELECT FROM retrieval.lineage_edges edge WHERE edge.child_id=n.id AND edge.parent_id=root.id)) THEN RETURN false; END IF;
  IF (SELECT array_agg(ancestor_id ORDER BY ancestor_id) FROM retrieval.source_ancestors WHERE source_id=proof.source_id)
   IS DISTINCT FROM (SELECT array_agg(ancestor_id ORDER BY ancestor_id) FROM historical_backtest.proof_heads WHERE proof_id=proof.id) THEN RETURN false; END IF;
  IF EXISTS(SELECT FROM historical_backtest.proof_heads frozen LEFT JOIN retrieval.source_nodes n ON n.id=frozen.ancestor_id LEFT JOIN retrieval.source_heads h ON h.source_id=n.id
   WHERE frozen.proof_id=proof.id AND (h.revision IS DISTINCT FROM frozen.revision OR h.status IS DISTINCT FROM 'published' OR n.digest IS DISTINCT FROM frozen.digest
    OR n.policy_class IS DISTINCT FROM frozen.policy_class OR n.known_at IS DISTINCT FROM frozen.known_at
    OR h.valid_from IS DISTINCT FROM frozen.valid_from OR h.valid_until IS DISTINCT FROM frozen.valid_until
    OR n.known_at>clock_timestamp() OR h.valid_from>clock_timestamp() OR h.valid_until<=clock_timestamp())) THEN RETURN false; END IF;
 END LOOP;
 RETURN total>0;
END $$;
CREATE FUNCTION historical_backtest.decide_subject(target uuid,expected_revision bigint,decision_name text,reason_text text,actor uuid,decision_id uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,historical_backtest,design,pg_temp AS $$
DECLARE subject historical_backtest.subjects; submission design.submission_records; old historical_backtest.subject_heads; actual_digest design.sha256;
BEGIN
 SELECT * INTO subject FROM historical_backtest.subjects WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'subject_missing' USING ERRCODE='P0002'; END IF;
 submission:=historical_backtest.lock_submission(subject.submission_id);
 SELECT b.digest INTO actual_digest FROM design.document_versions d JOIN design.blobs b ON b.id=d.blob_id WHERE d.id=submission.body_version_id;
 SELECT * INTO old FROM historical_backtest.subject_heads WHERE submission_id=submission.id FOR UPDATE;
 IF coalesce(old.revision,0) IS DISTINCT FROM expected_revision THEN RAISE EXCEPTION 'subject_revision_conflict' USING ERRCODE='40001'; END IF;
 IF decision_name IS NULL OR decision_name NOT IN ('pending','confirmed','rejected') OR length(btrim(reason_text)) NOT BETWEEN 1 AND 4000
  OR subject.body_version_id IS DISTINCT FROM submission.body_version_id OR subject.body_digest IS DISTINCT FROM actual_digest OR subject.submitted_at IS DISTINCT FROM submission.submitted_at
  OR subject.bid_id IS DISTINCT FROM submission.bid_id THEN RAISE EXCEPTION 'historical_subject_mismatch' USING ERRCODE='23514'; END IF;
 IF old.subject_id IS NOT NULL AND subject.id<>old.subject_id AND subject.supersedes_id IS DISTINCT FROM old.subject_id THEN RAISE EXCEPTION 'subject_supersession_conflict' USING ERRCODE='23514'; END IF;
 IF decision_name='confirmed' AND (subject.bidder_id IS NULL OR subject.round_id IS NULL OR subject.engineering_project_id IS NULL OR subject.identity_snapshot IS NULL OR NOT historical_backtest.current_proofs(target,NULL)) THEN RAISE EXCEPTION 'subject_proof_unavailable' USING ERRCODE='55000'; END IF;
 INSERT INTO historical_backtest.decisions VALUES(decision_id,'subject',target,decision_name,actor,reason_text,clock_timestamp());
 INSERT INTO historical_backtest.subject_heads VALUES(submission.id,target,decision_id,coalesce(old.revision,0)+1)
 ON CONFLICT(submission_id) DO UPDATE SET subject_id=excluded.subject_id,decision_id=excluded.decision_id,revision=excluded.revision;
 RETURN coalesce(old.revision,0)+1;
END $$;
CREATE FUNCTION historical_backtest.decide_claim(target uuid,expected_revision bigint,decision_name text,reason_text text,actor uuid,decision_id uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,historical_backtest,design,pg_temp AS $$
DECLARE claim historical_backtest.claims; submission design.submission_records; old historical_backtest.claim_heads;
BEGIN
 SELECT * INTO claim FROM historical_backtest.claims WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'claim_missing' USING ERRCODE='P0002'; END IF;
 submission:=historical_backtest.lock_submission(claim.submission_id);
 SELECT * INTO old FROM historical_backtest.claim_heads WHERE submission_id=submission.id AND document_version_id=claim.document_version_id FOR UPDATE;
 IF coalesce(old.revision,0) IS DISTINCT FROM expected_revision THEN RAISE EXCEPTION 'claim_revision_conflict' USING ERRCODE='40001'; END IF;
 IF decision_name IS NULL OR decision_name NOT IN ('pending','confirmed','rejected') OR length(btrim(reason_text)) NOT BETWEEN 1 AND 4000
  OR claim.bid_id IS DISTINCT FROM submission.bid_id OR NOT EXISTS(SELECT FROM design.document_versions d JOIN design.blobs b ON b.id=d.blob_id WHERE d.id=claim.document_version_id AND d.bid_id=claim.bid_id AND b.digest=claim.document_digest)
  OR (claim.subject_id IS NOT NULL AND NOT EXISTS(SELECT FROM historical_backtest.subjects s WHERE s.id=claim.subject_id AND s.submission_id=claim.submission_id AND s.bid_id=claim.bid_id)) THEN RAISE EXCEPTION 'historical_claim_mismatch' USING ERRCODE='23514'; END IF;
 IF old.claim_id IS NOT NULL AND claim.id<>old.claim_id AND claim.supersedes_id IS DISTINCT FROM old.claim_id THEN RAISE EXCEPTION 'claim_supersession_conflict' USING ERRCODE='23514'; END IF;
 IF decision_name='confirmed' AND (NOT historical_backtest.current_proofs(NULL,target)
  OR NOT EXISTS(SELECT FROM historical_backtest.facts f WHERE f.claim_id=target AND f.claimed_at IS NOT NULL)
  OR EXISTS(SELECT FROM historical_backtest.facts f WHERE f.claim_id=target AND f.claimed_at>clock_timestamp())
  OR (EXISTS(SELECT FROM historical_backtest.facts f WHERE f.claim_id=target AND f.fact_kind IN ('known_to_bidder','received_by_bidder') AND f.claimed_at IS NOT NULL)
   AND (NOT EXISTS(SELECT FROM historical_backtest.subject_heads head JOIN historical_backtest.decisions d ON d.id=head.decision_id WHERE head.submission_id=submission.id AND head.subject_id=claim.subject_id AND d.decision='confirmed') OR NOT historical_backtest.current_proofs(claim.subject_id,NULL)))
  OR EXISTS(SELECT FROM historical_backtest.facts f WHERE f.claim_id=target AND f.claimed_at IS NOT NULL AND NOT EXISTS(SELECT FROM historical_backtest.proofs p WHERE p.claim_id=target AND p.fact_kind=f.fact_kind))) THEN RAISE EXCEPTION 'claim_proof_unavailable' USING ERRCODE='55000'; END IF;
 INSERT INTO historical_backtest.decisions VALUES(decision_id,'claim',target,decision_name,actor,reason_text,clock_timestamp());
 INSERT INTO historical_backtest.claim_heads VALUES(submission.id,claim.document_version_id,target,decision_id,coalesce(old.revision,0)+1)
 ON CONFLICT(submission_id,document_version_id) DO UPDATE SET claim_id=excluded.claim_id,decision_id=excluded.decision_id,revision=excluded.revision;
 RETURN coalesce(old.revision,0)+1;
END $$;
-- Historical input execution remains unavailable in this first draft: no callable input
-- qualifier exists before exact package/rule/all-ancestor historical claims integration.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA historical_backtest FROM PUBLIC;
GRANT EXECUTE ON FUNCTION historical_backtest.decide_subject(uuid,bigint,text,text,uuid,uuid),historical_backtest.decide_claim(uuid,bigint,text,text,uuid,uuid) TO tender_api;
COMMIT;
