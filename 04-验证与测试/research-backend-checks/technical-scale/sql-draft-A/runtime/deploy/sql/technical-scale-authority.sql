-- Technical scale DRAFT A: static review then NEW isolated DB only.
-- First slice: sourced total denominator and scale; no executable aggregation/cert.
BEGIN;
CREATE SCHEMA technical_scale;
REVOKE ALL ON SCHEMA technical_scale FROM PUBLIC;
GRANT USAGE ON SCHEMA technical_scale TO tender_api;
CREATE TABLE technical_scale.versions(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES design.bids(id),
 purpose text NOT NULL CHECK(purpose IN ('current_prebid','historical_backtest')),context_id uuid NOT NULL,
 submission_id uuid,subject_id uuid REFERENCES historical_backtest.subjects(id),subject_decision_id uuid REFERENCES historical_backtest.decisions(id),
 material_snapshot_id uuid NOT NULL,identity_snapshot jsonb,
 technical_maximum numeric CHECK(technical_maximum>0 AND technical_maximum<=1000000000 AND technical_maximum NOT IN ('NaN','Infinity','-Infinity') AND scale(technical_maximum)<=8),
 scale_description text NOT NULL CHECK(length(scale_description)<=4000),aggregation jsonb NOT NULL,
 supersedes_id uuid REFERENCES technical_scale.versions(id),reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 4000),
 actor_id uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,submission_id) REFERENCES design.submission_records(bid_id,id),
 FOREIGN KEY(bid_id,material_snapshot_id) REFERENCES design.material_snapshots(bid_id,id),
 CHECK(coalesce(context_id=CASE purpose WHEN 'current_prebid' THEN bid_id ELSE submission_id END,false)),
 CHECK((purpose='current_prebid' AND submission_id IS NULL AND subject_id IS NULL AND subject_decision_id IS NULL)
  OR (purpose='historical_backtest' AND submission_id IS NOT NULL AND subject_id IS NOT NULL)),
 CHECK(coalesce(jsonb_typeof(aggregation)='object' AND aggregation ?& ARRAY['status','description'] AND aggregation->>'status' IN ('unknown','unsupported') AND jsonb_typeof(aggregation->'description')='string' AND length(btrim(aggregation->>'description')) BETWEEN 1 AND 4000,false))
);
CREATE TABLE technical_scale.evidence(
 id uuid PRIMARY KEY,version_id uuid NOT NULL REFERENCES technical_scale.versions(id),bid_id uuid NOT NULL,
 document_version_id uuid NOT NULL,blob_digest design.sha256 NOT NULL,parse_id uuid NOT NULL,evidence_id uuid NOT NULL,
 physical_page integer NOT NULL CHECK(physical_page>0),evidence_digest design.sha256 NOT NULL,
 source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),root_source_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),
 quote text NOT NULL CHECK(length(quote) BETWEEN 1 AND 16000),supported_fields text[] NOT NULL,
 FOREIGN KEY(bid_id,document_version_id) REFERENCES design.document_versions(bid_id,id),
 FOREIGN KEY(bid_id,parse_id,document_version_id) REFERENCES design.parse_runs(bid_id,id,document_version_id),
 FOREIGN KEY(bid_id,evidence_id) REFERENCES design.evidence_blocks(bid_id,id),
 CHECK(cardinality(supported_fields) BETWEEN 1 AND 3 AND supported_fields <@ ARRAY['technical_maximum','scale_description','aggregation']),
 UNIQUE(version_id,evidence_id,quote)
);
CREATE TABLE technical_scale.source_heads(
 evidence_id uuid NOT NULL REFERENCES technical_scale.evidence(id),ancestor_id uuid NOT NULL REFERENCES retrieval.source_nodes(id),
 revision bigint NOT NULL,digest design.sha256 NOT NULL,known_at timestamptz NOT NULL,valid_from timestamptz NOT NULL,valid_until timestamptz,
 PRIMARY KEY(evidence_id,ancestor_id)
);
CREATE TABLE technical_scale.claims(
 version_id uuid NOT NULL REFERENCES technical_scale.versions(id),claim_id uuid NOT NULL REFERENCES historical_backtest.claims(id),
 decision_id uuid NOT NULL REFERENCES historical_backtest.decisions(id),document_version_id uuid NOT NULL REFERENCES design.document_versions(id),PRIMARY KEY(version_id,claim_id),UNIQUE(version_id,document_version_id)
);
CREATE TABLE technical_scale.decisions(
 id uuid PRIMARY KEY,version_id uuid NOT NULL REFERENCES technical_scale.versions(id),
 state text NOT NULL CHECK(state IN ('pending','confirmed','withdrawn')),actor_id uuid NOT NULL REFERENCES design.members(id),
 command_id uuid NOT NULL UNIQUE REFERENCES design.commands(id),reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 4000),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE technical_scale.heads(
 bid_id uuid NOT NULL REFERENCES design.bids(id),purpose text NOT NULL,context_id uuid NOT NULL,
 version_id uuid NOT NULL REFERENCES technical_scale.versions(id),decision_id uuid NOT NULL REFERENCES technical_scale.decisions(id),
 revision bigint NOT NULL CHECK(revision>0),PRIMARY KEY(bid_id,purpose,context_id)
);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['versions','evidence','source_heads','claims','decisions'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON technical_scale.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',tab);
 END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA technical_scale FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_research_read,tender_score_read,tender_model_train,tender_model_evaluate,tender_model_score;
GRANT SELECT ON ALL TABLES IN SCHEMA technical_scale TO tender_api;
GRANT INSERT(id,bid_id,purpose,context_id,submission_id,subject_id,subject_decision_id,material_snapshot_id,identity_snapshot,technical_maximum,scale_description,aggregation,supersedes_id,reason,actor_id) ON technical_scale.versions TO tender_api;
GRANT INSERT ON technical_scale.evidence,technical_scale.source_heads,technical_scale.claims TO tender_api;

CREATE FUNCTION technical_scale.lock_version(target uuid) RETURNS technical_scale.versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,technical_scale,design,pg_temp AS $$
DECLARE result technical_scale.versions;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'read_committed_required' USING ERRCODE='55000'; END IF;
 SELECT * INTO result FROM technical_scale.versions WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'scale_version_missing' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(91143,1);
 PERFORM design.lock_worker_bid(result.bid_id);
 PERFORM pg_advisory_xact_lock(90127,1);
 RETURN result;
END $$;
CREATE FUNCTION technical_scale.declaration(content text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
DECLARE matched text[];
BEGIN
 -- Whole physical evidence block: a quote fragment cannot launder contrary context.
 matched:=regexp_match(content,'\A\s*(?:技术总分满分|技术总满分|技术部分满分|技术评分满分|技术标满分|技术总分)\s*(?:为|是|[:：])?\s*([0-9]+(?:\.[0-9]+)?)\s*分(?:\s*[，,；;]\s*(?:量尺|计分口径)\s*(?:为|是|[:：])\s*([^。\n；;]+))?\s*[。.]?\s*\Z');
 IF matched IS NULL OR matched[2] IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('maximum',matched[1]::numeric,'scale_description',btrim(matched[2]));
END $$;
CREATE FUNCTION technical_scale.current_context(target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,technical_scale,historical_backtest,design,pg_temp AS $$
DECLARE v technical_scale.versions; identity record; subject historical_backtest.subjects; actual uuid[]; expected uuid[];
BEGIN
 v:=technical_scale.lock_version(target);
 IF v.purpose='historical_backtest' THEN
  PERFORM historical_backtest.lock_submission(v.submission_id);
  SELECT * INTO subject FROM historical_backtest.subjects WHERE id=v.subject_id;
  RETURN subject.bid_id IS NOT DISTINCT FROM v.bid_id AND subject.submission_id IS NOT DISTINCT FROM v.submission_id
   AND subject.identity_snapshot IS NOT DISTINCT FROM v.identity_snapshot
   AND EXISTS(SELECT FROM historical_backtest.subject_heads h JOIN historical_backtest.decisions d ON d.id=h.decision_id
    WHERE h.submission_id=v.submission_id AND h.subject_id=v.subject_id AND h.decision_id=v.subject_decision_id AND d.object_kind='subject' AND d.object_id=v.subject_id AND d.decision='confirmed')
   AND EXISTS(SELECT FROM design.submission_records s JOIN design.document_versions body ON body.id=s.body_version_id JOIN design.blobs b ON b.id=body.blob_id
    WHERE s.id=v.submission_id AND s.bid_id=v.bid_id AND s.body_version_id=subject.body_version_id AND s.submitted_at=subject.submitted_at AND b.digest=subject.body_digest)
   AND EXISTS(SELECT FROM design.bid_rounds r WHERE r.id=subject.round_id AND r.engineering_project_id=subject.engineering_project_id
    AND subject.identity_snapshot->'round_no'=to_jsonb(r.round_no) AND subject.identity_snapshot->'lot' IS NOT DISTINCT FROM coalesce(to_jsonb(r.lot),'null'::jsonb))
   AND historical_backtest.current_proofs(v.subject_id,NULL);
 END IF;
 SELECT profile.*,r.engineering_project_id,r.lot,r.round_no,b.name AS bidder_name,p.title AS engineering_title,p.region,p.engineering_type,
  authority.id AS authority_id,authority.payload AS identity_record
 INTO identity FROM design.bid_profiles profile JOIN design.bid_rounds r ON r.id=profile.round_id JOIN design.bidders b ON b.id=profile.bidder_id JOIN design.engineering_projects p ON p.id=r.engineering_project_id
 JOIN LATERAL(SELECT id,payload FROM design.audit_entries WHERE bid_id=profile.bid_id AND event_type='identity.bound' ORDER BY coalesce((payload->>'identity_epoch')::bigint,0) DESC,created_at DESC,id DESC LIMIT 1) authority ON true
 WHERE profile.bid_id=v.bid_id;
 IF NOT FOUND OR identity.archived_at IS NOT NULL OR identity.phase IN ('closed','terminated')
  OR v.identity_snapshot->>'binding_status' IS DISTINCT FROM 'confirmed' OR v.identity_snapshot->>'authority_id' IS DISTINCT FROM identity.authority_id::text
  OR identity.identity_record->'confirmed' IS DISTINCT FROM 'true'::jsonb
  OR identity.identity_record->'after' IS DISTINCT FROM jsonb_build_object('bidder_id',identity.bidder_id,'bidder_name',identity.bidder_name,'round_id',identity.round_id,'engineering_project_id',identity.engineering_project_id,'engineering_title',identity.engineering_title,'region',identity.region,'engineering_type',identity.engineering_type,'lot',identity.lot,'round_no',identity.round_no)
  OR EXISTS(SELECT FROM jsonb_each(identity.identity_record->'after') field WHERE v.identity_snapshot->field.key IS DISTINCT FROM field.value) THEN RETURN false; END IF;
 -- A new unrelated body snapshot need not require another maximum confirmation.
 SELECT array_agg(document_version_id ORDER BY document_version_id) INTO actual FROM design.material_assignments WHERE bid_id=v.bid_id AND snapshot_id=identity.current_material_id AND role IN ('tender','supplement');
 SELECT array_agg(document_version_id ORDER BY document_version_id) INTO expected FROM design.material_assignments WHERE bid_id=v.bid_id AND snapshot_id=v.material_snapshot_id AND role IN ('tender','supplement');
 RETURN expected IS NOT NULL AND actual IS NOT DISTINCT FROM expected;
END $$;
CREATE FUNCTION technical_scale.qualified_version(target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,technical_scale,historical_backtest,design,retrieval,pg_temp AS $$
DECLARE v technical_scale.versions; ref technical_scale.evidence; block record; binding record; as_of timestamptz; expected uuid[]; actual uuid[]; supported text[]; parsed jsonb; total integer:=0;
BEGIN
 v:=technical_scale.lock_version(target);
 IF NOT coalesce(technical_scale.current_context(target),false) OR v.technical_maximum IS NULL OR length(btrim(v.scale_description))=0
  OR (SELECT count(*) FROM jsonb_object_keys(v.aggregation))<>2 THEN RETURN false; END IF;
 IF v.purpose='current_prebid' AND EXISTS(SELECT FROM technical_scale.claims WHERE version_id=target) THEN RETURN false; END IF;
 IF v.purpose='historical_backtest' AND (
  EXISTS(SELECT FROM technical_scale.claims map JOIN historical_backtest.claims claim ON claim.id=map.claim_id WHERE map.version_id=target AND map.document_version_id IS DISTINCT FROM claim.document_version_id)
  OR (SELECT array_agg(document_version_id ORDER BY document_version_id) FROM technical_scale.claims WHERE version_id=target) IS DISTINCT FROM (SELECT array_agg(document_version_id ORDER BY document_version_id) FROM (SELECT DISTINCT document_version_id FROM technical_scale.evidence WHERE version_id=target) documents)
 ) THEN RETURN false; END IF;
 IF v.purpose='current_prebid' THEN as_of:=v.created_at;
 ELSE SELECT submitted_at INTO as_of FROM design.submission_records WHERE id=v.submission_id; END IF;
 IF NOT EXISTS(SELECT FROM design.material_assignments a WHERE a.bid_id=v.bid_id AND a.snapshot_id=v.material_snapshot_id AND a.role='tender') THEN RETURN false; END IF;
 FOR ref IN SELECT * FROM technical_scale.evidence WHERE version_id=target LOOP
  total:=total+1;
  SELECT e.*,p.document_version_id,p.page_count,p.coverage,p.gaps,b.digest AS blob_digest,page.quality,page.gaps AS page_gaps
  INTO block FROM design.evidence_blocks e JOIN design.parse_runs p ON p.id=e.parse_id AND p.bid_id=e.bid_id
  JOIN design.document_versions d ON d.id=p.document_version_id AND d.bid_id=e.bid_id AND d.artifact_kind='source_original'
  JOIN design.blobs b ON b.id=d.blob_id JOIN design.pages page ON page.parse_id=e.parse_id AND page.physical_page=e.physical_page
  WHERE e.id=ref.evidence_id AND e.bid_id=v.bid_id;
  IF NOT FOUND OR ref.bid_id IS DISTINCT FROM v.bid_id OR ref.document_version_id IS DISTINCT FROM block.document_version_id
   OR ref.blob_digest IS DISTINCT FROM block.blob_digest OR ref.parse_id IS DISTINCT FROM block.parse_id
   OR ref.evidence_digest IS DISTINCT FROM block.digest OR ref.physical_page IS DISTINCT FROM block.physical_page
   OR ref.physical_page NOT BETWEEN 1 AND block.page_count OR block.quality<>'usable' OR block.page_gaps<>'[]'::jsonb
   OR block.kind NOT IN ('text','table') OR ref.quote<>block.text_content OR encode(sha256(convert_to(block.text_content,'UTF8')),'hex')<>block.digest::text
   OR NOT EXISTS(SELECT FROM design.parse_runs p WHERE p.id=ref.parse_id AND p.id=(SELECT p2.id FROM design.parse_runs p2 WHERE p2.bid_id=v.bid_id AND p2.document_version_id=ref.document_version_id ORDER BY p2.created_at DESC,p2.id DESC LIMIT 1))
   OR NOT EXISTS(SELECT FROM design.material_assignments a WHERE a.bid_id=v.bid_id AND a.snapshot_id=v.material_snapshot_id AND a.document_version_id=ref.document_version_id AND a.role IN ('tender','supplement'))
   OR NOT EXISTS(SELECT FROM retrieval.source_nodes n JOIN retrieval.source_nodes root ON root.id=ref.root_source_id JOIN retrieval.lineage_edges edge ON edge.child_id=n.id AND edge.parent_id=root.id
    WHERE n.id=ref.source_id AND n.kind='document' AND n.policy_class='prebid_content' AND n.document_version_id=ref.document_version_id AND n.digest=ref.blob_digest
     AND root.kind='document' AND root.policy_class='prebid_content' AND root.document_version_id=ref.document_version_id AND root.digest=ref.blob_digest)
  THEN RETURN false; END IF;
  IF ref.supported_fields && ARRAY['technical_maximum','scale_description'] THEN
   parsed:=technical_scale.declaration(block.text_content);
   IF parsed IS NULL OR (parsed->>'maximum')::numeric IS DISTINCT FROM v.technical_maximum OR parsed->>'scale_description' IS DISTINCT FROM v.scale_description THEN RETURN false; END IF;
  END IF;
  SELECT array_agg(ancestor_id ORDER BY ancestor_id) INTO expected FROM retrieval.source_ancestors WHERE source_id=ref.source_id;
  SELECT array_agg(ancestor_id ORDER BY ancestor_id) INTO actual FROM technical_scale.source_heads WHERE evidence_id=ref.id;
  IF actual IS DISTINCT FROM expected THEN RETURN false; END IF;
  IF EXISTS(SELECT FROM technical_scale.source_heads frozen JOIN retrieval.source_nodes n ON n.id=frozen.ancestor_id JOIN retrieval.source_heads h ON h.source_id=n.id
   WHERE frozen.evidence_id=ref.id AND (n.policy_class<>'prebid_content' OR h.status<>'published' OR h.revision<>frozen.revision OR n.digest<>frozen.digest OR n.known_at<>frozen.known_at
    OR h.valid_from<>frozen.valid_from OR h.valid_until IS DISTINCT FROM frozen.valid_until OR h.valid_from>clock_timestamp() OR (h.valid_until IS NOT NULL AND h.valid_until<=clock_timestamp())
    OR (v.purpose='current_prebid' AND (n.known_at>as_of OR h.valid_from>as_of OR (h.valid_until IS NOT NULL AND h.valid_until<=as_of))))) THEN RETURN false; END IF;
  IF v.purpose='historical_backtest' THEN
   SELECT c.*,bind.decision_id INTO binding FROM technical_scale.claims bind JOIN historical_backtest.claims c ON c.id=bind.claim_id WHERE bind.version_id=target AND c.document_version_id=ref.document_version_id;
   IF NOT FOUND OR binding.bid_id IS DISTINCT FROM v.bid_id OR binding.submission_id IS DISTINCT FROM v.submission_id OR binding.subject_id IS DISTINCT FROM v.subject_id OR binding.document_digest IS DISTINCT FROM ref.blob_digest
    OR NOT EXISTS(SELECT FROM historical_backtest.claim_heads h JOIN historical_backtest.decisions d ON d.id=h.decision_id WHERE h.submission_id=v.submission_id AND h.document_version_id=ref.document_version_id AND h.claim_id=binding.id AND h.decision_id=binding.decision_id AND d.object_kind='claim' AND d.object_id=binding.id AND d.decision='confirmed')
    OR NOT historical_backtest.current_proofs(NULL,binding.id) THEN RETURN false; END IF;
   IF EXISTS(SELECT required FROM unnest(ARRAY['content_existed','known_to_bidder','effective']) required WHERE NOT EXISTS(
    SELECT FROM historical_backtest.facts f WHERE f.claim_id=binding.id AND f.fact_kind=required AND f.claimed_at IS NOT NULL AND f.claimed_at<=as_of
     AND (f.valid_until IS NULL OR f.valid_until>as_of) AND EXISTS(SELECT FROM historical_backtest.proofs proof WHERE proof.claim_id=binding.id AND proof.fact_kind=required))) THEN RETURN false; END IF;
  END IF;
 END LOOP;
 SELECT array_agg(DISTINCT field) INTO supported FROM technical_scale.evidence e CROSS JOIN LATERAL unnest(e.supported_fields) field WHERE e.version_id=target;
 RETURN coalesce(total BETWEEN 1 AND 50 AND supported @> ARRAY['technical_maximum','scale_description'],false);
END $$;
CREATE FUNCTION technical_scale.apply_version(target uuid,expected_revision bigint,requested_state text,actor uuid,command uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,technical_scale,design,pg_temp AS $$
DECLARE v technical_scale.versions; old technical_scale.heads; decision uuid:=gen_random_uuid(); next_revision bigint;
BEGIN
 v:=technical_scale.lock_version(target);
 IF expected_revision IS NULL OR expected_revision<0 OR requested_state IS NULL OR requested_state NOT IN ('pending','confirmed') OR actor IS NULL OR command IS NULL OR actor IS DISTINCT FROM v.actor_id
  OR NOT EXISTS(SELECT FROM design.commands c WHERE c.id=command AND c.actor_id=actor AND (c.target_id=v.bid_id OR c.target_id=v.supersedes_id)) THEN RAISE EXCEPTION 'scale_command_invalid' USING ERRCODE='22023'; END IF;
 SELECT * INTO old FROM technical_scale.heads WHERE bid_id=v.bid_id AND purpose=v.purpose AND context_id=v.context_id FOR UPDATE;
 IF coalesce(old.revision,0) IS DISTINCT FROM expected_revision OR old.version_id IS DISTINCT FROM v.supersedes_id THEN RAISE EXCEPTION 'scale_head_changed' USING ERRCODE='40001'; END IF;
 IF EXISTS(SELECT FROM technical_scale.decisions WHERE version_id=target) THEN RAISE EXCEPTION 'scale_already_decided' USING ERRCODE='55000'; END IF;
 IF requested_state='confirmed' AND NOT coalesce(technical_scale.qualified_version(target),false) THEN RAISE EXCEPTION 'scale_unqualified' USING ERRCODE='55000'; END IF;
 IF v.purpose='current_prebid' AND EXISTS(SELECT FROM design.bid_profiles p WHERE p.bid_id=v.bid_id AND (p.archived_at IS NOT NULL OR p.phase IN ('closed','terminated'))) THEN RAISE EXCEPTION 'work_closed' USING ERRCODE='55000'; END IF;
 next_revision:=expected_revision+1;
 INSERT INTO technical_scale.decisions VALUES(decision,target,requested_state,actor,command,v.reason,clock_timestamp());
 INSERT INTO technical_scale.heads VALUES(v.bid_id,v.purpose,v.context_id,target,decision,next_revision)
 ON CONFLICT(bid_id,purpose,context_id) DO UPDATE SET version_id=EXCLUDED.version_id,decision_id=EXCLUDED.decision_id,revision=EXCLUDED.revision;
 RETURN next_revision;
END $$;
CREATE FUNCTION technical_scale.withdraw(target uuid,expected_revision bigint,reason_text text,actor uuid,command uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,technical_scale,design,pg_temp AS $$
DECLARE v technical_scale.versions; old technical_scale.heads; decision uuid:=gen_random_uuid();
BEGIN
 v:=technical_scale.lock_version(target);
 IF expected_revision IS NULL OR reason_text IS NULL OR length(btrim(reason_text)) NOT BETWEEN 1 AND 4000 OR actor IS NULL OR command IS NULL
  OR NOT EXISTS(SELECT FROM design.commands c WHERE c.id=command AND c.actor_id=actor AND c.target_id=target) THEN RAISE EXCEPTION 'scale_command_invalid' USING ERRCODE='22023'; END IF;
 SELECT * INTO old FROM technical_scale.heads WHERE bid_id=v.bid_id AND purpose=v.purpose AND context_id=v.context_id FOR UPDATE;
 IF old.version_id IS DISTINCT FROM target OR old.revision IS DISTINCT FROM expected_revision THEN RAISE EXCEPTION 'scale_head_changed' USING ERRCODE='40001'; END IF;
 IF v.purpose='current_prebid' AND EXISTS(SELECT FROM design.bid_profiles p WHERE p.bid_id=v.bid_id AND (p.archived_at IS NOT NULL OR p.phase IN ('closed','terminated'))) THEN RAISE EXCEPTION 'work_closed' USING ERRCODE='55000'; END IF;
 INSERT INTO technical_scale.decisions VALUES(decision,target,'withdrawn',actor,command,reason_text,clock_timestamp());
 UPDATE technical_scale.heads SET decision_id=decision,revision=revision+1 WHERE bid_id=v.bid_id AND purpose=v.purpose AND context_id=v.context_id;
 RETURN expected_revision+1;
END $$;
CREATE FUNCTION technical_scale.current_version(target uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,technical_scale,pg_temp AS $$
DECLARE v technical_scale.versions;
BEGIN
 v:=technical_scale.lock_version(target);
 RETURN EXISTS(SELECT FROM technical_scale.heads h JOIN technical_scale.decisions d ON d.id=h.decision_id WHERE h.bid_id=v.bid_id AND h.purpose=v.purpose AND h.context_id=v.context_id AND h.version_id=v.id AND d.version_id=v.id AND d.state='confirmed') AND technical_scale.qualified_version(target);
END $$;
CREATE FUNCTION technical_scale.prevent_decided_append() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,technical_scale,pg_temp AS $$
DECLARE target uuid;
BEGIN
 IF TG_TABLE_NAME='source_heads' THEN SELECT version_id INTO target FROM technical_scale.evidence WHERE id=NEW.evidence_id; ELSE target:=NEW.version_id; END IF;
 PERFORM technical_scale.lock_version(target);
 IF EXISTS(SELECT FROM technical_scale.decisions WHERE version_id=target) THEN RAISE EXCEPTION 'scale_version_decided' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER decided_guard BEFORE INSERT ON technical_scale.evidence FOR EACH ROW EXECUTE FUNCTION technical_scale.prevent_decided_append();
CREATE TRIGGER decided_guard BEFORE INSERT ON technical_scale.source_heads FOR EACH ROW EXECUTE FUNCTION technical_scale.prevent_decided_append();
CREATE TRIGGER decided_guard BEFORE INSERT ON technical_scale.claims FOR EACH ROW EXECUTE FUNCTION technical_scale.prevent_decided_append();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA technical_scale FROM PUBLIC;
GRANT EXECUTE ON FUNCTION technical_scale.apply_version(uuid,bigint,text,uuid,uuid),technical_scale.withdraw(uuid,bigint,text,uuid,uuid),technical_scale.current_version(uuid) TO tender_api;
-- No source/proof/label or technical maximum is exposed to any worker by this SQL.
-- Frozen input links and score feature projection require a separate reviewed increment.
COMMIT;
