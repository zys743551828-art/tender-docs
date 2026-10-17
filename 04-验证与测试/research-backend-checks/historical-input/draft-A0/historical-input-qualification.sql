-- Historical input qualification DRAFT A. Architecture review -> NEW full DB only.
-- Requires historical-backtest.sql E. Does not change the original system observation times.
BEGIN;
CREATE SCHEMA historical_input;
REVOKE ALL ON SCHEMA historical_input FROM PUBLIC;
GRANT USAGE ON SCHEMA historical_input TO tender_api;
CREATE TABLE historical_input.seals(
 input_id uuid PRIMARY KEY REFERENCES historical_backtest.inputs(id),
 authority_digest design.sha256 NOT NULL,rule_count integer NOT NULL CHECK(rule_count BETWEEN 1 AND 1000),
 actor_id uuid NOT NULL REFERENCES design.members(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- API prepares inert clean content outside transactions. The SQL authority independently
-- compares the entire parsed document with the expected body/rule projection before sealing.
-- Score workers will get only an owned chapter RPC, never arbitrary table access or blob keys.
CREATE TABLE historical_input.chapter_payloads(
 chapter_id uuid PRIMARY KEY REFERENCES historical_backtest.chapters(id),
 raw_content text NOT NULL,byte_digest design.sha256 NOT NULL,
 CHECK(octet_length(raw_content) BETWEEN 1 AND 2097152 AND jsonb_typeof(raw_content::jsonb)='object'),
 CHECK(encode(sha256(convert_to(raw_content,'UTF8')),'hex')=byte_digest::text)
);
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON historical_input.seals FOR EACH ROW EXECUTE FUNCTION design.append_only();
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON historical_input.chapter_payloads FOR EACH ROW EXECUTE FUNCTION design.append_only();
REVOKE ALL ON ALL TABLES IN SCHEMA historical_input FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_research_read,tender_score_read,tender_model_train,tender_model_evaluate,tender_model_score;
GRANT SELECT ON historical_input.seals,historical_input.chapter_payloads TO tender_api;
GRANT INSERT ON historical_input.chapter_payloads TO tender_api;

CREATE FUNCTION historical_input.lock_scope(target uuid) RETURNS historical_backtest.inputs
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,historical_input,historical_backtest,design,retrieval,pg_temp AS $$
DECLARE authority historical_backtest.inputs;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'fresh_read_committed_required' USING ERRCODE='55000'; END IF;
 SELECT * INTO authority FROM historical_backtest.inputs WHERE id=target;
 IF NOT FOUND THEN RAISE EXCEPTION 'historical_input_missing' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(91143,1);
 PERFORM design.lock_worker_bid(authority.bid_id);
 PERFORM pg_advisory_xact_lock(90127,1);
 -- Header is append-only. Actual current heads must be read AFTER waiting for these locks.
 RETURN authority;
END $$;
CREATE FUNCTION historical_input.clean_structure(value jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,historical_input,pg_temp AS $$
DECLARE child record;
BEGIN
 IF jsonb_typeof(value)='object' THEN
  FOR child IN SELECT key,value FROM jsonb_each(value) LOOP
   IF child.key=ANY(ARRAY['outcome','outcomes','label','score','official_score','judges','chat','messages','summary','prediction','post_submission_revision']) OR NOT historical_input.clean_structure(child.value) THEN RETURN false; END IF;
  END LOOP;
 ELSIF jsonb_typeof(value)='array' THEN
  FOR child IN SELECT element FROM jsonb_array_elements(value) element LOOP
   IF NOT historical_input.clean_structure(child.element) THEN RETURN false; END IF;
  END LOOP;
 END IF;
 RETURN true;
END $$;
CREATE FUNCTION historical_input.valid_band_points(value jsonb,maximum numeric) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
DECLARE points numeric;
BEGIN
 IF jsonb_typeof(value) NOT IN ('string','number') OR maximum IS NULL OR maximum<=0 THEN RETURN false; END IF;
 BEGIN points:=(value#>>'{}')::numeric;
 EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
 END;
 RETURN points NOT IN ('NaN','Infinity','-Infinity') AND points>=0 AND points<=maximum;
END $$;
CREATE FUNCTION historical_input.expected_chapter(target uuid,target_rule uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,historical_input,historical_backtest,design,pg_temp AS $$
 SELECT jsonb_build_object('purpose','formal_score',
  'rule',jsonb_build_object('id',r.id,'statement',r.statement,'conditions',r.conditions,'exceptions',r.exceptions,
   'maximum',r.max_points::text,'bands',(SELECT jsonb_agg(jsonb_build_object('id',r.id::text||':band:'||ordinal::text,'title',band->'label','description',band->'description','points',band->'points') ORDER BY ordinal) FROM jsonb_array_elements(r.bands) WITH ORDINALITY AS b(band,ordinal))),
  'body_version_id',i.body_version_id,'parse_id',body.parse_id,'method_digest',i.method_digest,'digest',chapter.digest,
  'sections',jsonb_build_array(jsonb_build_object('id','full-body',
   'pages',(SELECT jsonb_agg(page.physical_page ORDER BY page.physical_page) FROM design.pages page WHERE page.parse_id=body.parse_id),
   'blocks',(SELECT jsonb_agg(jsonb_build_object('evidence_id',e.id,'physical_page',e.physical_page,'text',e.text_content,'digest',e.digest) ORDER BY e.physical_page,e.ordinal,e.id) FROM design.evidence_blocks e WHERE e.parse_id=body.parse_id))))
 FROM historical_backtest.inputs i JOIN historical_backtest.input_parses body ON body.input_id=i.id AND body.document_version_id=i.body_version_id
 JOIN design.rule_items r ON r.rule_set_id=i.rule_set_id AND r.bid_id=i.bid_id AND r.kind='scoring' AND r.id=target_rule
 JOIN historical_backtest.chapters chapter ON chapter.input_id=i.id AND chapter.rule_id=r.id AND chapter.body_parse_id=body.parse_id
 WHERE i.id=target;
$$;

CREATE FUNCTION historical_input.validate(target uuid,require_sealed boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,historical_input,historical_backtest,design,retrieval,pg_temp AS $$
DECLARE i historical_backtest.inputs; submission design.submission_records; subject historical_backtest.subjects;
 member record; required_kind text; actual_ids uuid[]; expected_ids uuid[]; package jsonb; required_count integer;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RETURN false; END IF;
 i:=historical_input.lock_scope(target);
 IF require_sealed AND NOT EXISTS(SELECT FROM historical_input.seals s WHERE s.input_id=target) THEN RETURN false; END IF;
 SELECT * INTO submission FROM design.submission_records WHERE id=i.submission_id;
 IF submission.id IS NULL OR i.bid_id IS DISTINCT FROM submission.bid_id OR i.body_version_id IS DISTINCT FROM submission.body_version_id
  OR i.historical_as_of IS DISTINCT FROM submission.submitted_at OR i.historical_as_of>i.created_at OR i.created_at>clock_timestamp()
  OR i.prediction_timing<>'retrospective' OR i.execution_purpose<>'historical_backtest' OR NOT i.research_only
  OR NOT EXISTS(SELECT FROM design.submission_heads h WHERE h.bid_id=i.bid_id AND h.submission_id=submission.id)
  OR NOT EXISTS(SELECT FROM design.document_versions d JOIN design.blobs b ON b.id=d.blob_id WHERE d.id=i.body_version_id AND d.bid_id=i.bid_id AND d.artifact_kind='bid_body' AND b.digest=i.body_digest)
  OR i.method_digest::text<>'276a2c953aa86d935d46aabba432f7503ab6ce8081a9375b490ecb2172fd4d48' THEN RETURN false; END IF;
 SELECT * INTO subject FROM historical_backtest.subjects WHERE id=i.subject_id;
 IF subject.id IS NULL OR subject.bid_id IS DISTINCT FROM i.bid_id OR subject.submission_id IS DISTINCT FROM submission.id
  OR subject.body_version_id IS DISTINCT FROM i.body_version_id OR subject.body_digest IS DISTINCT FROM i.body_digest OR subject.submitted_at IS DISTINCT FROM i.historical_as_of
  OR NOT EXISTS(SELECT FROM historical_backtest.subject_heads h JOIN historical_backtest.decisions d ON d.id=h.decision_id
   WHERE h.submission_id=submission.id AND h.subject_id=subject.id AND h.decision_id=i.subject_decision_id AND d.object_kind='subject' AND d.object_id=subject.id AND d.decision='confirmed')
  OR NOT EXISTS(SELECT FROM design.bid_rounds r WHERE r.id=subject.round_id AND r.engineering_project_id=subject.engineering_project_id
   AND subject.identity_snapshot->'round_no'=to_jsonb(r.round_no) AND subject.identity_snapshot->'lot' IS NOT DISTINCT FROM coalesce(to_jsonb(r.lot),'null'::jsonb))
  OR NOT historical_backtest.current_proofs(subject.id,NULL) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT FROM design.rule_sets r WHERE r.id=i.rule_set_id AND r.bid_id=i.bid_id AND r.material_id=i.material_snapshot_id AND r.coverage='complete') THEN RETURN false; END IF;
 -- The selected historical material snapshot is explicit; current profile/file replacement
 -- does not retrospectively change an old version's historical effectiveness.
 IF NOT EXISTS(SELECT FROM design.material_assignments a WHERE a.snapshot_id=i.material_snapshot_id AND a.bid_id=i.bid_id AND a.role='tender')
  OR EXISTS(SELECT FROM design.material_assignments a WHERE a.snapshot_id=i.material_snapshot_id AND (a.bid_id<>i.bid_id OR a.role NOT IN ('tender','supplement'))) THEN RETURN false; END IF;
 SELECT array_agg(id ORDER BY id) INTO expected_ids FROM (
  SELECT i.body_version_id AS id UNION SELECT a.document_version_id FROM design.material_assignments a WHERE a.snapshot_id=i.material_snapshot_id AND a.bid_id=i.bid_id
 ) documents;
 SELECT array_agg(document_version_id ORDER BY document_version_id) INTO actual_ids FROM historical_backtest.input_parses WHERE input_id=target;
 IF actual_ids IS DISTINCT FROM expected_ids THEN RETURN false; END IF;
 SELECT array_agg(claim.document_version_id ORDER BY claim.document_version_id) INTO actual_ids
 FROM historical_backtest.input_claims bind JOIN historical_backtest.claims claim ON claim.id=bind.claim_id WHERE bind.input_id=target;
 IF actual_ids IS DISTINCT FROM expected_ids THEN RETURN false; END IF;
 FOR member IN SELECT bind.*,claim.document_version_id,claim.document_digest,claim.subject_id,claim.bid_id,claim.submission_id,claim.declared_origin
  FROM historical_backtest.input_claims bind JOIN historical_backtest.claims claim ON claim.id=bind.claim_id WHERE bind.input_id=target LOOP
  IF member.bid_id IS DISTINCT FROM i.bid_id OR member.submission_id IS DISTINCT FROM submission.id OR member.subject_id IS DISTINCT FROM subject.id
   OR NOT EXISTS(SELECT FROM design.document_versions d JOIN design.blobs b ON b.id=d.blob_id WHERE d.id=member.document_version_id AND d.bid_id=i.bid_id AND b.digest=member.document_digest)
   OR NOT EXISTS(SELECT FROM historical_backtest.claim_heads h JOIN historical_backtest.decisions d ON d.id=h.decision_id WHERE h.submission_id=submission.id AND h.document_version_id=member.document_version_id
    AND h.claim_id=member.claim_id AND h.decision_id=member.decision_id AND d.object_kind='claim' AND d.object_id=member.claim_id AND d.decision='confirmed')
   OR NOT historical_backtest.current_proofs(NULL,member.claim_id)
   OR (member.purpose='body' AND member.document_version_id<>i.body_version_id)
   OR (member.purpose IN ('tender','supplement') AND NOT EXISTS(SELECT FROM design.material_assignments a WHERE a.snapshot_id=i.material_snapshot_id AND a.document_version_id=member.document_version_id AND a.role=member.purpose))
   OR member.purpose='prebid_fact' THEN RETURN false; END IF;
  FOREACH required_kind IN ARRAY ARRAY['content_existed','known_to_bidder'] LOOP
   IF NOT EXISTS(SELECT FROM historical_backtest.facts f WHERE f.claim_id=member.claim_id AND f.fact_kind=required_kind
    AND f.claimed_at IS NOT NULL AND f.claimed_at<=i.historical_as_of AND EXISTS(SELECT FROM historical_backtest.proofs proof WHERE proof.claim_id=f.claim_id AND proof.fact_kind=f.fact_kind)) THEN RETURN false; END IF;
  END LOOP;
  IF member.purpose IN ('tender','supplement') AND NOT EXISTS(SELECT FROM historical_backtest.facts f WHERE f.claim_id=member.claim_id AND f.fact_kind='effective' AND f.claimed_at<=i.historical_as_of
   AND (f.valid_until IS NULL OR f.valid_until>i.historical_as_of) AND EXISTS(SELECT FROM historical_backtest.proofs proof WHERE proof.claim_id=f.claim_id AND proof.fact_kind=f.fact_kind)) THEN RETURN false; END IF;
  IF EXISTS(SELECT FROM historical_backtest.facts existed JOIN historical_backtest.facts fact ON fact.claim_id=existed.claim_id WHERE existed.claim_id=member.claim_id AND existed.fact_kind='content_existed' AND fact.claimed_at<existed.claimed_at) THEN RETURN false; END IF;
 END LOOP;
 -- Origin is not self-certified real by selecting a request enum. A real provenance
 -- authority and training/certification path is a separate gate. Synthetic stays synthetic.
 IF EXISTS(SELECT FROM historical_backtest.input_claims bind JOIN historical_backtest.claims claim ON claim.id=bind.claim_id WHERE bind.input_id=target AND claim.declared_origin='synthetic') OR subject.declared_origin='synthetic' THEN
  IF i.origin<>'synthetic' THEN RETURN false; END IF;
 ELSIF i.origin<>'uncertain' THEN RETURN false; END IF;
 FOR member IN SELECT * FROM historical_backtest.input_parses WHERE input_id=target LOOP
  IF NOT EXISTS(SELECT FROM design.parse_runs p JOIN design.document_versions d ON d.id=p.document_version_id JOIN design.blobs b ON b.id=d.blob_id
   JOIN retrieval.source_nodes n ON n.id=member.source_id JOIN retrieval.source_nodes root ON root.id=member.root_source_id
   WHERE p.id=member.parse_id AND p.document_version_id=member.document_version_id AND p.bid_id=i.bid_id AND d.bid_id=i.bid_id
    AND p.id=(SELECT latest.id FROM design.parse_runs latest WHERE latest.document_version_id=d.id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
    AND p.coverage='complete' AND p.gaps='[]'::jsonb AND p.page_count>0 AND p.page_count=(SELECT count(*) FROM design.pages page WHERE page.parse_id=p.id)
    AND NOT EXISTS(SELECT FROM design.pages page WHERE page.parse_id=p.id AND (page.quality<>'usable' OR page.gaps<>'[]'::jsonb))
    AND n.kind='document' AND root.kind='document' AND n.document_version_id=d.id AND root.document_version_id=d.id AND n.digest=b.digest AND root.digest=b.digest
    AND EXISTS(SELECT FROM retrieval.lineage_edges edge WHERE edge.child_id=n.id AND edge.parent_id=root.id)) THEN RETURN false; END IF;
  SELECT array_agg(ancestor_id ORDER BY ancestor_id) INTO expected_ids FROM retrieval.source_ancestors WHERE source_id=member.source_id;
  SELECT array_agg(ancestor_id ORDER BY ancestor_id) INTO actual_ids FROM historical_backtest.input_source_heads WHERE input_id=target AND source_id=member.source_id;
  IF actual_ids IS DISTINCT FROM expected_ids THEN RETURN false; END IF;
 END LOOP;
 IF EXISTS(SELECT FROM historical_backtest.input_source_heads frozen LEFT JOIN historical_backtest.input_parses parse ON parse.input_id=frozen.input_id AND parse.source_id=frozen.source_id
  LEFT JOIN retrieval.source_nodes n ON n.id=frozen.ancestor_id LEFT JOIN retrieval.source_heads h ON h.source_id=n.id
  WHERE frozen.input_id=target AND (parse.input_id IS NULL OR h.revision IS DISTINCT FROM frozen.revision OR h.status IS DISTINCT FROM 'published'
   OR n.digest IS DISTINCT FROM frozen.digest OR n.known_at IS DISTINCT FROM frozen.known_at OR n.policy_class IS DISTINCT FROM 'prebid_content' OR n.kind IS DISTINCT FROM 'document'
   OR h.valid_from IS DISTINCT FROM frozen.valid_from OR h.valid_until IS DISTINCT FROM frozen.valid_until
   OR h.valid_from>i.created_at OR h.valid_from>clock_timestamp() OR h.valid_until<=clock_timestamp()
   OR NOT EXISTS(SELECT FROM historical_backtest.input_claims claim_binding JOIN historical_backtest.claims claim ON claim.id=claim_binding.claim_id WHERE claim_binding.input_id=target AND claim.document_version_id=n.document_version_id))) THEN RETURN false; END IF;
 -- Exact complete body evidence, plus each necessary scoring rule's original evidence.
 SELECT array_agg(id ORDER BY id) INTO expected_ids FROM (
  SELECT e.id FROM historical_backtest.input_parses p JOIN design.evidence_blocks e ON e.parse_id=p.parse_id WHERE p.input_id=target AND p.document_version_id=i.body_version_id
  UNION SELECT evidence_id FROM design.rule_items r CROSS JOIN LATERAL unnest(r.evidence_ids) evidence_id WHERE r.rule_set_id=i.rule_set_id AND r.bid_id=i.bid_id AND r.kind='scoring'
 ) blocks;
 SELECT array_agg(evidence_id ORDER BY evidence_id) INTO actual_ids FROM historical_backtest.input_evidence WHERE input_id=target;
 IF actual_ids IS DISTINCT FROM expected_ids OR expected_ids IS NULL THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM historical_backtest.input_evidence frozen LEFT JOIN design.evidence_blocks e ON e.id=frozen.evidence_id
  LEFT JOIN historical_backtest.input_parses p ON p.input_id=frozen.input_id AND p.parse_id=frozen.parse_id
  WHERE frozen.input_id=target AND (p.input_id IS NULL OR e.bid_id IS DISTINCT FROM i.bid_id OR e.parse_id IS DISTINCT FROM frozen.parse_id OR e.physical_page IS DISTINCT FROM frozen.physical_page
   OR e.digest IS DISTINCT FROM frozen.evidence_digest OR e.kind NOT IN ('text','table') OR encode(sha256(convert_to(e.text_content,'UTF8')),'hex') IS DISTINCT FROM e.digest::text)) THEN RETURN false; END IF;
 SELECT count(*) INTO required_count FROM design.rule_items WHERE bid_id=i.bid_id AND rule_set_id=i.rule_set_id AND kind='scoring';
 IF required_count NOT BETWEEN 1 AND 1000 THEN RETURN false; END IF;
 SELECT array_agg(id ORDER BY id) INTO expected_ids FROM design.rule_items WHERE bid_id=i.bid_id AND rule_set_id=i.rule_set_id AND kind='scoring';
 SELECT array_agg(rule_id ORDER BY rule_id) INTO actual_ids FROM historical_backtest.chapters WHERE input_id=target;
 IF actual_ids IS DISTINCT FROM expected_ids THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM design.rule_items r WHERE r.bid_id=i.bid_id AND r.rule_set_id=i.rule_set_id AND r.kind='scoring' AND
  (r.provenance_status<>'effective' OR r.max_points IS NULL OR jsonb_typeof(r.bands)<>'array' OR jsonb_array_length(r.bands) NOT BETWEEN 1 AND 100
   OR EXISTS(SELECT FROM jsonb_array_elements(r.bands) band WHERE jsonb_typeof(band->'label') IS DISTINCT FROM 'string' OR jsonb_typeof(band->'description') IS DISTINCT FROM 'string' OR NOT historical_input.valid_band_points(band->'points',r.max_points)))) THEN RETURN false; END IF;
 FOR member IN SELECT chapter.*,payload.raw_content,payload.byte_digest,blob.digest AS blob_digest,blob.byte_size,blob.media_type
  FROM historical_backtest.chapters chapter LEFT JOIN historical_input.chapter_payloads payload ON payload.chapter_id=chapter.id
  LEFT JOIN design.blobs blob ON blob.id=chapter.manifest_blob_id WHERE chapter.input_id=target LOOP
  package:=historical_input.expected_chapter(target,member.rule_id);
  IF package IS NULL OR NOT historical_input.clean_structure(package) OR member.raw_content IS NULL OR member.raw_content::jsonb IS DISTINCT FROM package
   OR member.byte_digest IS DISTINCT FROM member.blob_digest OR octet_length(member.raw_content) IS DISTINCT FROM member.byte_size
   OR member.media_type IS DISTINCT FROM 'application/vnd.tender.chapter-package+json' THEN RETURN false; END IF;
 END LOOP;
 IF require_sealed AND NOT EXISTS(SELECT FROM historical_input.seals s WHERE s.input_id=target AND s.rule_count=required_count) THEN RETURN false; END IF;
 RETURN true;
END $$;
CREATE FUNCTION historical_input.seal_input(target uuid,actor uuid) RETURNS design.sha256
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,historical_input,historical_backtest,design,pg_temp AS $$
DECLARE authority historical_backtest.inputs; expected_digest text; rule_count integer;
BEGIN
 authority:=historical_input.lock_scope(target);
 IF actor IS NULL OR authority.actor_id IS DISTINCT FROM actor OR NOT historical_input.validate(target,false) THEN RAISE EXCEPTION 'historical_input_not_qualified' USING ERRCODE='55000'; END IF;
 SELECT encode(sha256(convert_to(jsonb_build_object('input',to_jsonb(authority),
  'claims',(SELECT jsonb_agg(to_jsonb(v) ORDER BY claim_id) FROM historical_backtest.input_claims v WHERE input_id=target),
  'parses',(SELECT jsonb_agg(to_jsonb(v) ORDER BY document_version_id) FROM historical_backtest.input_parses v WHERE input_id=target),
  'heads',(SELECT jsonb_agg(to_jsonb(v) ORDER BY source_id,ancestor_id) FROM historical_backtest.input_source_heads v WHERE input_id=target),
  'evidence',(SELECT jsonb_agg(to_jsonb(v) ORDER BY evidence_id) FROM historical_backtest.input_evidence v WHERE input_id=target),
  'chapters',(SELECT jsonb_agg(jsonb_build_object('chapter',to_jsonb(v),'payload_bytes',p.byte_digest) ORDER BY v.rule_id) FROM historical_backtest.chapters v JOIN historical_input.chapter_payloads p ON p.chapter_id=v.id WHERE v.input_id=target))::text,'UTF8')),'hex') INTO expected_digest;
 SELECT count(*) INTO rule_count FROM historical_backtest.chapters WHERE input_id=target;
 INSERT INTO historical_input.seals VALUES(target,expected_digest,rule_count,actor,clock_timestamp());
 RETURN expected_digest::design.sha256;
END $$;
CREATE FUNCTION historical_input.current_input(target uuid) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,historical_input,pg_temp AS $$ SELECT historical_input.validate(target,true); $$;
CREATE FUNCTION historical_input.prevent_sealed_append() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,historical_input,historical_backtest,design,pg_temp AS $$
DECLARE target uuid;
BEGIN
 IF TG_TABLE_SCHEMA='historical_input' THEN SELECT input_id INTO target FROM historical_backtest.chapters WHERE id=NEW.chapter_id;
 ELSE target:=NEW.input_id; END IF;
 PERFORM historical_input.lock_scope(target);
 IF EXISTS(SELECT FROM historical_input.seals WHERE input_id=target) THEN RAISE EXCEPTION 'historical_input_already_sealed' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['input_claims','input_parses','input_source_heads','input_evidence','chapters'] LOOP
 EXECUTE format('CREATE TRIGGER sealed_input_guard BEFORE INSERT ON historical_backtest.%I FOR EACH ROW EXECUTE FUNCTION historical_input.prevent_sealed_append()',tab);
 END LOOP;
END $$;
CREATE TRIGGER sealed_input_guard BEFORE INSERT ON historical_input.chapter_payloads FOR EACH ROW EXECUTE FUNCTION historical_input.prevent_sealed_append();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA historical_input FROM PUBLIC;
GRANT EXECUTE ON FUNCTION historical_input.seal_input(uuid,uuid),historical_input.current_input(uuid) TO tender_api;
-- No worker/CPU grants. A later owned score execution authority must derive target input
-- from its operation/job and fence; arbitrary historical input IDs never grant content reads.
COMMIT;
