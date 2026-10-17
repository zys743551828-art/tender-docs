-- FR029 independent fresh-initialization DRAFT C. Do not execute or hotpatch existing DBs.
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
CREATE TABLE model_governance.chapter_bindings(
 cohort_id uuid NOT NULL,sample_id uuid NOT NULL,chapter_package_id uuid NOT NULL REFERENCES design.research_chapter_authorities(chapter_package_id),
 PRIMARY KEY(cohort_id,sample_id,chapter_package_id),FOREIGN KEY(cohort_id,sample_id) REFERENCES model_governance.members(cohort_id,sample_id)
);
CREATE TABLE model_governance.parse_bindings(
 cohort_id uuid NOT NULL,sample_id uuid NOT NULL,parse_id uuid NOT NULL REFERENCES design.parse_runs(id),
 document_version_id uuid NOT NULL REFERENCES design.document_versions(id),source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),
 original_source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),purpose text NOT NULL CHECK(purpose IN ('body','rule','official')),
 PRIMARY KEY(cohort_id,sample_id,parse_id),FOREIGN KEY(cohort_id,sample_id) REFERENCES model_governance.members(cohort_id,sample_id)
);
CREATE TABLE model_governance.evidence_bindings(
 cohort_id uuid NOT NULL,sample_id uuid NOT NULL,parse_id uuid NOT NULL,evidence_id uuid NOT NULL REFERENCES design.evidence_blocks(id),
 physical_page integer NOT NULL,ordinal integer NOT NULL,digest design.sha256 NOT NULL,
 PRIMARY KEY(cohort_id,sample_id,evidence_id),FOREIGN KEY(cohort_id,sample_id,parse_id) REFERENCES model_governance.parse_bindings(cohort_id,sample_id,parse_id)
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
 FOREACH tab IN ARRAY ARRAY['protocols','cohorts','members','source_bindings','chapter_bindings','parse_bindings','evidence_bindings','exports','results','evaluations','exposures','exposure_members','model_records','route_events'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON model_governance.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',tab);
 END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA model_governance FROM PUBLIC,tender_worker,tender_research_worker,tender_research_read,tender_score_read,tender_model_train,tender_model_evaluate,tender_model_score;
GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA model_governance TO tender_api;
GRANT UPDATE(state,generation) ON model_governance.jobs TO tender_api;

CREATE FUNCTION model_governance.check_origin() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,model_governance,design,pg_temp AS $$
DECLARE expected text;
BEGIN
 IF TG_TABLE_NAME='cohorts' THEN
  SELECT origin INTO expected FROM model_governance.protocols WHERE id=NEW.protocol_id;
 ELSE
  SELECT c.origin INTO expected FROM model_governance.results r JOIN model_governance.jobs j ON j.id=r.job_id JOIN model_governance.cohorts c ON c.id=j.cohort_id WHERE r.job_id=NEW.training_job_id;
 END IF;
 IF NEW.origin IS DISTINCT FROM expected THEN RAISE EXCEPTION 'origin_lineage_mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER origin_lineage BEFORE INSERT ON model_governance.cohorts FOR EACH ROW EXECUTE FUNCTION model_governance.check_origin();
CREATE TRIGGER origin_lineage BEFORE INSERT ON model_governance.model_records FOR EACH ROW EXECUTE FUNCTION model_governance.check_origin();

-- Caller must enter with 91143, then ALL cohort bids sorted, then graph, before final effects.
-- Function takes the same locks itself so a narrow role cannot omit the lock scope.
CREATE FUNCTION model_governance.current_cohort(target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,retrieval,pg_temp AS $$
DECLARE member record;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RETURN false; END IF;
 PERFORM pg_advisory_xact_lock(91143,1);
 FOR member IN SELECT DISTINCT scope.bid_id FROM (
  SELECT bid_id FROM model_governance.members WHERE cohort_id=target
  UNION SELECT s.bid_id FROM design.samples s WHERE s.source_group_id IN (SELECT group_id FROM model_governance.members WHERE cohort_id=target)
 ) scope ORDER BY scope.bid_id LOOP
  PERFORM design.lock_worker_bid(member.bid_id);
 END LOOP;
 PERFORM pg_advisory_xact_lock(90127,1);
 IF NOT EXISTS(SELECT FROM model_governance.members WHERE cohort_id=target) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.members m JOIN model_governance.cohorts c ON c.id=m.cohort_id
  JOIN design.outcomes o ON o.id=m.outcome_id JOIN design.submission_records submission ON submission.id=m.submission_id
  WHERE m.cohort_id=target AND (m.known_at IS DISTINCT FROM o.known_at OR m.submitted_at IS DISTINCT FROM submission.submitted_at
   OR m.known_at<m.submitted_at OR m.known_at>c.created_at OR o.score_kind<>'technical_total' OR o.value IS NULL
   OR (m.purpose IN ('fit','development','calibration') AND (m.known_at>=c.test_start OR m.submitted_at>=c.test_start))
   OR (m.purpose='final' AND m.submitted_at<c.test_start))) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.members m CROSS JOIN LATERAL
  (VALUES('group',m.group_id::text),('project',m.project_id::text),('body',m.body_digest::text),('template',m.template_key),('near_duplicate',m.near_duplicate_key)) v(kind,identity)
  WHERE m.cohort_id=target AND m.purpose<>'excluded' AND v.identity IS NOT NULL
  GROUP BY v.kind,v.identity HAVING count(DISTINCT m.purpose)>1) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.members frozen
  LEFT JOIN design.samples sample ON sample.id=frozen.sample_id
  LEFT JOIN design.submission_records submission ON submission.id=frozen.submission_id
  LEFT JOIN design.submission_heads sh ON sh.bid_id=frozen.bid_id
  LEFT JOIN design.outcome_heads oh ON oh.submission_id=frozen.submission_id
  LEFT JOIN design.decision_heads grouping ON grouping.bid_id=frozen.bid_id AND grouping.object_kind='sample_group' AND grouping.object_id=frozen.sample_id
  LEFT JOIN design.decision_heads verification ON verification.bid_id=frozen.bid_id AND verification.object_kind='outcome' AND verification.object_id=frozen.outcome_id
  LEFT JOIN design.bid_profiles profile ON profile.bid_id=frozen.bid_id
  LEFT JOIN design.bid_rounds round_identity ON round_identity.id=profile.round_id
  LEFT JOIN design.bidders bidder_identity ON bidder_identity.id=profile.bidder_id
  LEFT JOIN design.engineering_projects project_identity ON project_identity.id=round_identity.engineering_project_id
  LEFT JOIN design.audit_entries identity_authority ON identity_authority.id=frozen.identity_authority_id
  LEFT JOIN design.decisions verified ON verified.id=frozen.verification_decision_id
  LEFT JOIN design.outcomes official ON official.id=frozen.outcome_id
  LEFT JOIN design.document_versions official_document ON official_document.id=official.source_version_id
  WHERE frozen.cohort_id=target AND (sample.admission IS DISTINCT FROM 'admitted'
   OR sample.source_group_id IS DISTINCT FROM frozen.group_id OR sample.outcome_id IS DISTINCT FROM frozen.outcome_id
   OR sh.submission_id IS DISTINCT FROM frozen.submission_id OR oh.outcome_id IS DISTINCT FROM frozen.outcome_id
   OR submission.evidence->'identity' IS DISTINCT FROM frozen.identity_snapshot
   OR grouping.decision_id IS DISTINCT FROM frozen.grouping_decision_id
   OR verification.decision_id IS DISTINCT FROM frozen.verification_decision_id
   OR profile.bidder_id IS DISTINCT FROM (frozen.identity_snapshot->>'bidder_id')::uuid
   OR profile.round_id IS DISTINCT FROM (frozen.identity_snapshot->>'round_id')::uuid
   OR verified.decision IS DISTINCT FROM 'verified' OR verified.reason::jsonb->>'published_at' IS NULL
   OR (verified.reason::jsonb->>'published_at')::timestamptz>frozen.known_at
   OR (verified.reason::jsonb->>'published_at')::timestamptz<frozen.submitted_at
   OR official_document.obtained_at>frozen.known_at
   OR frozen.identity_snapshot->>'binding_status' IS DISTINCT FROM 'confirmed'
   OR frozen.identity_snapshot->>'authority_id' IS DISTINCT FROM frozen.identity_authority_id::text
   OR identity_authority.payload->'confirmed' IS DISTINCT FROM 'true'::jsonb
   OR identity_authority.payload->'after' IS DISTINCT FROM jsonb_build_object(
    'bidder_id',profile.bidder_id,'bidder_name',bidder_identity.name,'round_id',profile.round_id,
    'engineering_project_id',round_identity.engineering_project_id,'engineering_title',project_identity.title,
    'region',project_identity.region,'engineering_type',project_identity.engineering_type,
    'lot',round_identity.lot,'round_no',round_identity.round_no)
   OR frozen.identity_authority_id IS DISTINCT FROM (SELECT a.id FROM design.audit_entries a WHERE a.bid_id=frozen.bid_id AND a.event_type='identity.bound' ORDER BY coalesce((a.payload->>'identity_epoch')::bigint,0) DESC,a.created_at DESC,a.id DESC LIMIT 1)
  )) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.source_bindings frozen
  LEFT JOIN retrieval.source_nodes n ON n.id=frozen.ancestor_id LEFT JOIN retrieval.source_heads h ON h.source_id=n.id LEFT JOIN design.document_versions source_document ON source_document.id=n.document_version_id
  WHERE frozen.cohort_id=target AND (n.digest IS DISTINCT FROM frozen.digest OR n.policy_class IS DISTINCT FROM frozen.policy_class OR n.kind IS DISTINCT FROM 'document' OR source_document.bid_id IS DISTINCT FROM (SELECT m.bid_id FROM model_governance.members m WHERE m.cohort_id=target AND m.sample_id=frozen.sample_id)
   OR h.status IS DISTINCT FROM 'published' OR h.revision IS DISTINCT FROM frozen.revision
   OR n.known_at IS DISTINCT FROM frozen.known_at OR h.valid_from IS DISTINCT FROM frozen.valid_from OR h.valid_until IS DISTINCT FROM frozen.valid_until
   OR h.valid_from>clock_timestamp() OR h.valid_until<=clock_timestamp()
   OR n.known_at>CASE WHEN frozen.policy_class='official_label' THEN (SELECT m.known_at FROM model_governance.members m WHERE m.cohort_id=target AND m.sample_id=frozen.sample_id)
     ELSE (SELECT least(m.submitted_at,min(authority.as_of)) FROM model_governance.members m JOIN model_governance.chapter_bindings cb ON cb.cohort_id=m.cohort_id AND cb.sample_id=m.sample_id JOIN design.research_chapter_authorities authority ON authority.chapter_package_id=cb.chapter_package_id WHERE m.cohort_id=target AND m.sample_id=frozen.sample_id GROUP BY m.submitted_at) END
   OR h.valid_from>CASE WHEN frozen.policy_class='official_label' THEN (SELECT c.created_at FROM model_governance.cohorts c WHERE c.id=target)
     ELSE (SELECT min(authority.as_of) FROM model_governance.chapter_bindings cb JOIN design.research_chapter_authorities authority ON authority.chapter_package_id=cb.chapter_package_id WHERE cb.cohort_id=target AND cb.sample_id=frozen.sample_id) END
   OR n.known_at>(SELECT c.created_at FROM model_governance.cohorts c WHERE c.id=target)
   OR NOT EXISTS(SELECT FROM retrieval.source_ancestors a WHERE a.source_id=frozen.source_id AND a.ancestor_id=frozen.ancestor_id))) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.source_bindings selected JOIN retrieval.source_ancestors a ON a.source_id=selected.source_id
  WHERE selected.cohort_id=target AND NOT EXISTS(SELECT FROM model_governance.source_bindings frozen WHERE frozen.cohort_id=target AND frozen.sample_id=selected.sample_id AND frozen.source_id=a.source_id AND frozen.ancestor_id=a.ancestor_id)) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.chapter_bindings cb JOIN model_governance.members m ON m.cohort_id=cb.cohort_id AND m.sample_id=cb.sample_id
  JOIN design.chapter_packages cp ON cp.id=cb.chapter_package_id JOIN design.submission_records submission ON submission.id=m.submission_id
  JOIN design.review_runs rr ON rr.id=cp.review_id JOIN design.review_inputs ri ON ri.id=rr.input_id
  WHERE cb.cohort_id=target AND (cp.bid_id<>m.bid_id OR cp.review_id IS DISTINCT FROM submission.review_id OR ri.body_version_id IS DISTINCT FROM submission.body_version_id
   OR NOT EXISTS(SELECT FROM design.rule_items rule WHERE rule.id=cp.rule_id AND rule.bid_id=m.bid_id AND rule.rule_set_id=(ri.manifest->>'rule_set_id')::uuid AND rule.kind='scoring')
   OR NOT EXISTS(SELECT FROM model_governance.parse_bindings p WHERE p.cohort_id=target AND p.sample_id=m.sample_id AND p.purpose='body' AND p.parse_id=cp.parse_id))) THEN RETURN false; END IF;
 -- Recheck exact original/parse/page/evidence; unchanged source heads do not authorize a replaced parse.
 IF EXISTS(SELECT FROM model_governance.parse_bindings frozen JOIN model_governance.members m ON m.cohort_id=frozen.cohort_id AND m.sample_id=frozen.sample_id
  LEFT JOIN design.parse_runs p ON p.id=frozen.parse_id LEFT JOIN design.document_versions d ON d.id=frozen.document_version_id
  LEFT JOIN design.blobs blob ON blob.id=d.blob_id LEFT JOIN retrieval.source_nodes n ON n.id=frozen.source_id
  LEFT JOIN retrieval.source_nodes original ON original.id=frozen.original_source_id
  WHERE frozen.cohort_id=target AND (p.bid_id IS DISTINCT FROM m.bid_id OR p.document_version_id IS DISTINCT FROM frozen.document_version_id
   OR p.id IS DISTINCT FROM (SELECT latest.id FROM design.parse_runs latest WHERE latest.document_version_id=frozen.document_version_id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
   OR p.coverage IS DISTINCT FROM 'complete' OR p.gaps IS DISTINCT FROM '[]'::jsonb OR p.page_count<=0
   OR p.page_count<>(SELECT count(*) FROM design.pages page WHERE page.parse_id=p.id)
   OR EXISTS(SELECT FROM design.pages page WHERE page.parse_id=p.id AND (page.quality<>'usable' OR page.gaps<>'[]'::jsonb))
   OR n.document_version_id IS DISTINCT FROM d.id OR original.document_version_id IS DISTINCT FROM d.id
   OR n.kind IS DISTINCT FROM 'document' OR original.kind IS DISTINCT FROM 'document'
   OR n.digest IS DISTINCT FROM blob.digest OR original.digest IS DISTINCT FROM blob.digest
   OR NOT EXISTS(SELECT FROM retrieval.lineage_edges e WHERE e.child_id=n.id AND e.parent_id=original.id)
   OR NOT EXISTS(SELECT FROM model_governance.source_bindings b WHERE b.cohort_id=target AND b.sample_id=m.sample_id AND b.source_id=n.id AND b.ancestor_id=n.id)
   OR (frozen.purpose='body' AND d.id IS DISTINCT FROM (SELECT s.body_version_id FROM design.submission_records s WHERE s.id=m.submission_id))
   OR (frozen.purpose='official' AND d.id IS DISTINCT FROM (SELECT o.source_version_id FROM design.outcomes o WHERE o.id=m.outcome_id))
   OR (frozen.purpose='rule' AND NOT EXISTS(SELECT FROM model_governance.chapter_bindings cb JOIN design.chapter_packages cp ON cp.id=cb.chapter_package_id JOIN design.review_runs rr ON rr.id=cp.review_id JOIN design.review_inputs ri ON ri.id=rr.input_id JOIN design.material_assignments a ON a.snapshot_id=ri.material_snapshot_id AND a.bid_id=m.bid_id AND a.role IN ('tender','supplement') WHERE cb.cohort_id=target AND cb.sample_id=m.sample_id AND a.document_version_id=d.id))
  )) THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM model_governance.evidence_bindings frozen JOIN model_governance.members m ON m.cohort_id=frozen.cohort_id AND m.sample_id=frozen.sample_id
  LEFT JOIN design.evidence_blocks e ON e.id=frozen.evidence_id LEFT JOIN design.pages page ON page.parse_id=e.parse_id AND page.physical_page=e.physical_page
  WHERE frozen.cohort_id=target AND (e.bid_id IS DISTINCT FROM m.bid_id OR e.parse_id IS DISTINCT FROM frozen.parse_id
   OR e.physical_page IS DISTINCT FROM frozen.physical_page OR e.ordinal IS DISTINCT FROM frozen.ordinal OR e.digest IS DISTINCT FROM frozen.digest OR page.quality IS DISTINCT FROM 'usable')) THEN RETURN false; END IF;
 FOR member IN SELECT * FROM model_governance.members WHERE cohort_id=target LOOP
  IF NOT EXISTS(SELECT FROM model_governance.chapter_bindings cb WHERE cb.cohort_id=target AND cb.sample_id=member.sample_id) THEN RETURN false; END IF;
  IF (SELECT array_agg(DISTINCT cp.rule_id ORDER BY cp.rule_id) FROM model_governance.chapter_bindings cb JOIN design.chapter_packages cp ON cp.id=cb.chapter_package_id WHERE cb.cohort_id=target AND cb.sample_id=member.sample_id)
   IS DISTINCT FROM (SELECT array_agg(rule.id ORDER BY rule.id) FROM design.submission_records submission JOIN design.review_runs rr ON rr.id=submission.review_id JOIN design.review_inputs ri ON ri.id=rr.input_id JOIN design.rule_items rule ON rule.rule_set_id=(ri.manifest->>'rule_set_id')::uuid AND rule.bid_id=member.bid_id AND rule.kind='scoring' WHERE submission.id=member.submission_id) THEN RETURN false; END IF;
  IF (SELECT array_agg(DISTINCT e.id ORDER BY e.id) FROM model_governance.chapter_bindings cb JOIN design.chapter_packages cp ON cp.id=cb.chapter_package_id JOIN design.evidence_blocks e ON e.parse_id=cp.parse_id WHERE cb.cohort_id=target AND cb.sample_id=member.sample_id)
   IS DISTINCT FROM (SELECT array_agg(e.evidence_id ORDER BY e.evidence_id) FROM model_governance.evidence_bindings e JOIN model_governance.parse_bindings p USING(cohort_id,sample_id,parse_id) WHERE e.cohort_id=target AND e.sample_id=member.sample_id AND p.purpose='body') THEN RETURN false; END IF;
  IF (SELECT array_agg(DISTINCT evidence_id ORDER BY evidence_id) FROM model_governance.chapter_bindings cb JOIN design.chapter_packages cp ON cp.id=cb.chapter_package_id JOIN design.rule_items r ON r.id=cp.rule_id CROSS JOIN LATERAL unnest(r.evidence_ids) evidence_id WHERE cb.cohort_id=target AND cb.sample_id=member.sample_id)
   IS DISTINCT FROM (SELECT array_agg(e.evidence_id ORDER BY e.evidence_id) FROM model_governance.evidence_bindings e JOIN model_governance.parse_bindings p USING(cohort_id,sample_id,parse_id) WHERE e.cohort_id=target AND e.sample_id=member.sample_id AND p.purpose='rule') THEN RETURN false; END IF;
  IF (SELECT array_agg(DISTINCT evidence_id ORDER BY evidence_id) FROM design.decisions d CROSS JOIN LATERAL unnest(d.evidence_ids) evidence_id WHERE d.id=member.verification_decision_id AND d.decision='verified')
   IS DISTINCT FROM (SELECT array_agg(e.evidence_id ORDER BY e.evidence_id) FROM model_governance.evidence_bindings e JOIN model_governance.parse_bindings p USING(cohort_id,sample_id,parse_id) WHERE e.cohort_id=target AND e.sample_id=member.sample_id AND p.purpose='official') THEN RETURN false; END IF;
 END LOOP;
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
 SELECT * INTO job FROM model_governance.jobs WHERE id=target FOR UPDATE;
 intended:=CASE job.kind WHEN 'train' THEN 'tender_model_train' ELSE 'tender_model_evaluate' END;
 IF NOT FOUND OR job.state<>'running' OR NOT pg_has_role(session_user,intended,'member') OR NOT EXISTS(SELECT FROM model_governance.leases l WHERE l.job_id=target AND l.owner_token=token AND l.fence=expected_fence AND l.generation=job.generation AND l.lease_until>clock_timestamp()) THEN RAISE EXCEPTION 'lease_invalid' USING ERRCODE='55000'; END IF;
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
 SELECT DISTINCT v.kind,v.identity,exposure FROM design.samples sample
 JOIN design.submission_records submission ON submission.id=sample.submission_id
 JOIN design.document_versions document ON document.id=submission.body_version_id JOIN design.blobs body ON body.id=document.blob_id
 LEFT JOIN design.bid_profiles profile ON profile.bid_id=sample.bid_id LEFT JOIN design.bid_rounds round_identity ON round_identity.id=profile.round_id
 LEFT JOIN design.decision_heads grouping ON grouping.bid_id=sample.bid_id AND grouping.object_kind='sample_group' AND grouping.object_id=sample.id
 LEFT JOIN design.decisions metadata ON metadata.id=grouping.decision_id
 CROSS JOIN LATERAL (VALUES('sample',sample.id::text),('group',sample.source_group_id::text),('project',round_identity.engineering_project_id::text),('body',body.digest::text),('template',metadata.reason::jsonb->>'template_key'),('near_duplicate',metadata.reason::jsonb->>'near_duplicate_key')) v(kind,identity)
 WHERE sample.source_group_id IN (SELECT m.group_id FROM model_governance.members m WHERE m.cohort_id=job.cohort_id AND m.purpose='final') AND v.identity IS NOT NULL;
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
CREATE FUNCTION model_governance.renew_job(target uuid,token uuid,expected_fence bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,pg_temp AS $$
DECLARE job model_governance.jobs;
BEGIN
 job:=model_governance.assert_lease(target,token,expected_fence);
 UPDATE model_governance.leases SET lease_until=clock_timestamp()+interval '120 seconds'
 WHERE job_id=target AND owner_token=token AND fence=expected_fence AND generation=job.generation AND lease_until>clock_timestamp();
 RETURN FOUND;
END $$;
CREATE FUNCTION model_governance.finish_job(target uuid,token uuid,expected_fence bigint,result_digest design.sha256,result_payload jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,model_governance,design,pg_temp AS $$
DECLARE job model_governance.jobs;
BEGIN
 job:=model_governance.assert_lease(target,token,expected_fence);
 IF result_payload->>'origin' IS DISTINCT FROM (SELECT c.origin FROM model_governance.cohorts c WHERE c.id=job.cohort_id)
  OR result_payload->>'formal_eligibility' IS DISTINCT FROM 'unavailable'
  OR result_payload->>'schema' IS DISTINCT FROM (CASE job.kind WHEN 'train' THEN 'frozen-training-result/1' ELSE 'independent-final-evaluation/1' END)
  OR ((SELECT c.origin FROM model_governance.cohorts c WHERE c.id=job.cohort_id)='synthetic' AND result_payload->'research_only' IS DISTINCT FROM 'true'::jsonb) THEN
  RAISE EXCEPTION 'result_origin_or_purpose_invalid' USING ERRCODE='23514';
 END IF;
 IF encode(sha256(convert_to(result_payload::text,'UTF8')),'hex')<>result_digest THEN RAISE EXCEPTION 'result_digest_invalid' USING ERRCODE='22023'; END IF;
 INSERT INTO model_governance.results VALUES(target,result_digest,result_payload,token,expected_fence,clock_timestamp());
 UPDATE model_governance.jobs SET state='succeeded' WHERE id=target;
 RETURN true;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA model_governance FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_governance.claim_job(uuid,uuid),model_governance.finish_job(uuid,uuid,bigint,design.sha256,jsonb),model_governance.renew_job(uuid,uuid,bigint) TO tender_model_train,tender_model_evaluate;
GRANT EXECUTE ON FUNCTION model_governance.read_training(uuid,uuid,bigint) TO tender_model_train;
GRANT EXECUTE ON FUNCTION model_governance.open_final(uuid,uuid,bigint,uuid),model_governance.read_final(uuid,uuid,bigint) TO tender_model_evaluate;
-- Scoring has zero business reads/execute until the separate score authority+ledger draft is reviewed.
COMMIT;
