-- Forward fresh-environment DDL. Requires core/domain/bid-packages/identity-v2/retrieval/worker-functions.
-- Canonical rule_items/decisions remain the only clause/confirmation authority.
BEGIN;
SET search_path=design,public;

CREATE FUNCTION bid_requirement_envelope_shape(value jsonb) RETURNS boolean LANGUAGE plpgsql IMMUTABLE
 SET search_path=pg_catalog,design AS $$
DECLARE key text;
BEGIN
 IF jsonb_typeof(value) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
 FOREACH key IN ARRAY ARRAY['id','bid_id'] LOOP
  IF NOT package_json_text(value->key) THEN RETURN false; END IF;
 END LOOP;
 IF value->>'version'='bid-package-requirements/1' THEN
  FOREACH key IN ARRAY ARRAY['package_id','target_revision_id','authority_revision_id','rule_set_id','actor_id'] LOOP
   IF NOT package_json_text(value->key) THEN RETURN false; END IF;
  END LOOP;
  FOREACH key IN ARRAY ARRAY['package_digest','authority_digest','rule_set_digest','identity_digest'] LOOP
   IF jsonb_typeof(value->key) IS DISTINCT FROM 'string' OR (value->>key ~ '^[a-f0-9]{64}$') IS NOT TRUE THEN RETURN false; END IF;
  END LOOP;
  IF value->'predecessor_digest' IS DISTINCT FROM 'null'::jsonb AND (jsonb_typeof(value->'predecessor_digest') IS DISTINCT FROM 'string' OR (value->>'predecessor_digest' ~ '^[a-f0-9]{64}$') IS NOT TRUE) THEN RETURN false; END IF;
 ELSIF value->>'version'='bid-package-coverage/1' THEN
  IF NOT package_json_text(value->'inventory_id') OR NOT package_json_text(value->'actor_id') THEN RETURN false; END IF;
  FOREACH key IN ARRAY ARRAY['inventory_digest','package_digest'] LOOP
   IF jsonb_typeof(value->key) IS DISTINCT FROM 'string' OR (value->>key ~ '^[a-f0-9]{64}$') IS NOT TRUE THEN RETURN false; END IF;
  END LOOP;
 ELSE RETURN false;
 END IF;
 RETURN true;
END $$;

CREATE FUNCTION bid_requirement_uuid_array(value jsonb) RETURNS boolean LANGUAGE plpgsql IMMUTABLE
 SET search_path=pg_catalog AS $$
BEGIN
 IF jsonb_typeof(value) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
 RETURN jsonb_array_length(value)<=500 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(value) x(v)
 WHERE jsonb_typeof(v) IS DISTINCT FROM 'string' OR (v#>>'{}' ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$') IS NOT TRUE)
 AND (SELECT count(*) FROM jsonb_array_elements(value))=(SELECT count(DISTINCT v) FROM jsonb_array_elements(value) x(v));
END $$;

-- Also serialize the absent-head INSERT case with package writes. Row locks alone
-- cannot protect a decision head which does not exist yet. Keep native authority.
CREATE FUNCTION bid_requirement_decision_serial_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,design AS $$
BEGIN
 IF TG_OP='UPDATE' AND (OLD.bid_id IS DISTINCT FROM NEW.bid_id OR OLD.object_kind IS DISTINCT FROM NEW.object_kind OR OLD.object_id IS DISTINCT FROM NEW.object_id) THEN
  RAISE EXCEPTION 'requirement_decision_head_rebind_forbidden' USING ERRCODE='23514';
 END IF;
 IF TG_OP='DELETE' THEN
  PERFORM id FROM design.bids WHERE id=OLD.bid_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'requirement_decision_bid_missing' USING ERRCODE='23514'; END IF;
  RETURN OLD;
 END IF;
 PERFORM id FROM design.bids WHERE id=NEW.bid_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'requirement_decision_bid_missing' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER requirement_decision_serial BEFORE INSERT OR UPDATE OR DELETE ON decision_heads
 FOR EACH ROW EXECUTE FUNCTION bid_requirement_decision_serial_guard();

-- Carryover is also native current-decision authority, including its absent key.
-- Reuse the existing narrow worker lock: workers do not get UPDATE on bids.
CREATE FUNCTION bid_requirement_carryover_serial_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,design AS $$
BEGIN
 PERFORM design.lock_worker_bid(NEW.bid_id);
 RETURN NEW;
END $$;
CREATE TRIGGER requirement_carryover_serial BEFORE INSERT ON rule_carryovers
 FOR EACH ROW EXECUTE FUNCTION bid_requirement_carryover_serial_guard();

-- All known source aliases participate in the snapshot, even a newly inserted one.
CREATE FUNCTION bid_requirement_source_serial_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(90127,1);
 RETURN NEW;
END $$;
CREATE TRIGGER requirement_source_serial BEFORE INSERT ON retrieval.source_nodes
 FOR EACH ROW EXECUTE FUNCTION design.bid_requirement_source_serial_guard();

CREATE TABLE bid_package_requirement_inventories(
 id uuid PRIMARY KEY, bid_id uuid NOT NULL REFERENCES bid_profiles(bid_id),
 package_id uuid NOT NULL, target_revision_id uuid NOT NULL, authority_revision_id uuid NOT NULL REFERENCES bid_package_revisions(id),
 rule_set_id uuid NOT NULL, revision bigint NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
 predecessor_digest sha256, digest sha256 NOT NULL, manifest jsonb NOT NULL,
 actor_id uuid NOT NULL REFERENCES members(id), created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(package_id,bid_id) REFERENCES bid_packages(id,bid_id),
 FOREIGN KEY(package_id,target_revision_id) REFERENCES bid_package_revisions(package_id,id),
 FOREIGN KEY(bid_id,rule_set_id) REFERENCES rule_sets(bid_id,id),
 UNIQUE(target_revision_id,revision),UNIQUE(target_revision_id,id),UNIQUE(target_revision_id,digest),UNIQUE(bid_id,id),
 FOREIGN KEY(target_revision_id,predecessor_digest) REFERENCES bid_package_requirement_inventories(target_revision_id,digest),
 CHECK((revision=1)=(predecessor_digest IS NULL)),
 CHECK(bid_requirement_envelope_shape(manifest)),
 CHECK(package_json_keys(manifest,ARRAY['version','id','bid_id','package_id','target_revision_id','package_digest','authority_revision_id','authority_digest','rule_set_id','rule_set_digest','revision','predecessor_digest','identity_digest','source_snapshot','requirements','inventory_complete','extraction_binding','review_ready','actor_id']) IS TRUE),
 CHECK((manifest->>'version'='bid-package-requirements/1' AND manifest->'inventory_complete'='false'::jsonb AND manifest->'review_ready'='false'::jsonb AND manifest->>'extraction_binding'='unproven_legacy_rule_set') IS TRUE),
 CHECK((manifest->>'id'=id::text AND manifest->>'bid_id'=bid_id::text AND manifest->>'package_id'=package_id::text AND manifest->>'target_revision_id'=target_revision_id::text AND manifest->>'authority_revision_id'=authority_revision_id::text AND manifest->>'rule_set_id'=rule_set_id::text) IS TRUE),
 CHECK((manifest->>'actor_id'=actor_id::text) IS TRUE),
 CHECK(package_json_integer(manifest->'revision',1) IS TRUE AND (manifest->>'revision')::bigint=revision),
 CHECK(manifest->>'predecessor_digest' IS NOT DISTINCT FROM predecessor_digest::text),
 CHECK((jsonb_typeof(manifest->'requirements')='array' AND jsonb_typeof(manifest->'source_snapshot')='array') IS TRUE),
 CHECK(encode(sha256(convert_to(package_canonical_json(manifest),'UTF8')),'hex')=digest)
);
CREATE TABLE bid_package_requirements(
 inventory_id uuid NOT NULL, bid_id uuid NOT NULL, ordinal integer NOT NULL CHECK(ordinal>=0),
 rule_id uuid NOT NULL, decision_id uuid, requirement_digest sha256 NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(inventory_id,rule_id), UNIQUE(inventory_id,ordinal), UNIQUE(inventory_id,rule_id,requirement_digest),
 FOREIGN KEY(bid_id,inventory_id) REFERENCES bid_package_requirement_inventories(bid_id,id),
 FOREIGN KEY(bid_id,rule_id) REFERENCES rule_items(bid_id,id),
 FOREIGN KEY(bid_id,decision_id) REFERENCES decisions(bid_id,id),
 CHECK(package_json_keys(payload,ARRAY['rule_id','rule_digest','rule_snapshot','decision_id','decision_snapshot','requirement','requirement_digest','source_bound','annotation']) IS TRUE),
 CHECK((payload->>'rule_id'=rule_id::text AND payload->>'requirement_digest'=requirement_digest::text) IS TRUE),
 CHECK(payload->>'decision_id' IS NOT DISTINCT FROM decision_id::text),
 CHECK(encode(sha256(convert_to(package_canonical_json(payload->'requirement'),'UTF8')),'hex')=requirement_digest)
);
CREATE TABLE bid_package_requirement_heads(
 target_revision_id uuid PRIMARY KEY REFERENCES bid_package_revisions(id), inventory_id uuid NOT NULL,
 revision bigint NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),digest sha256 NOT NULL,
 FOREIGN KEY(target_revision_id,inventory_id) REFERENCES bid_package_requirement_inventories(target_revision_id,id)
);
CREATE TABLE bid_package_coverage_attempts(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,inventory_id uuid NOT NULL,digest sha256 NOT NULL,manifest jsonb NOT NULL,
 actor_id uuid NOT NULL REFERENCES members(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(bid_id,inventory_id) REFERENCES bid_package_requirement_inventories(bid_id,id),UNIQUE(id,inventory_id),UNIQUE(bid_id,id),
 CHECK(bid_requirement_envelope_shape(manifest)),
 CHECK(package_json_keys(manifest,ARRAY['version','id','bid_id','inventory_id','inventory_digest','package_digest','actor_id','results','assessment','review_ready']) IS TRUE),
 CHECK((manifest->>'version'='bid-package-coverage/1' AND manifest->'review_ready'='false'::jsonb AND manifest->>'id'=id::text AND manifest->>'bid_id'=bid_id::text AND manifest->>'inventory_id'=inventory_id::text AND manifest->>'actor_id'=actor_id::text AND jsonb_typeof(manifest->'results')='array' AND manifest->'assessment'->'complete'='false'::jsonb) IS TRUE),
 CHECK(encode(sha256(convert_to(package_canonical_json(manifest),'UTF8')),'hex')=digest)
);
CREATE TABLE bid_package_coverage_results(
 attempt_id uuid NOT NULL,inventory_id uuid NOT NULL,rule_id uuid NOT NULL,ordinal integer NOT NULL CHECK(ordinal>=0),
 requirement_digest sha256 NOT NULL,payload jsonb NOT NULL,
 PRIMARY KEY(attempt_id,rule_id),UNIQUE(attempt_id,ordinal),
 FOREIGN KEY(attempt_id,inventory_id) REFERENCES bid_package_coverage_attempts(id,inventory_id),
 FOREIGN KEY(inventory_id,rule_id,requirement_digest) REFERENCES bid_package_requirements(inventory_id,rule_id,requirement_digest),
 CHECK(package_json_keys(payload,ARRAY['coverage','basis_type','evidence_ids']) IS TRUE),
 CHECK(bid_requirement_uuid_array(payload->'evidence_ids')),
 CHECK((payload->'coverage'->>'requirement_id'=rule_id::text AND payload->'coverage'->>'requirement_digest'=requirement_digest::text) IS TRUE),
 CHECK((payload->>'basis_type' IN ('human_scope_declaration','human_observation') AND jsonb_typeof(payload->'evidence_ids')='array') IS TRUE)
);

CREATE FUNCTION bid_requirement_source_snapshot(target uuid,authority uuid) RETURNS jsonb LANGUAGE sql VOLATILE
 SET search_path=pg_catalog,design,retrieval AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('source_id',s.source_id,'ancestor_id',n.id,'document_version_id',n.document_version_id,
 'policy_class',n.policy_class,'known_at',n.known_at,'revision',h.revision,'status',h.status,'valid_from',h.valid_from,'valid_until',h.valid_until)
 ORDER BY s.source_id,n.id),'[]'::jsonb)
 FROM retrieval.source_ancestors s JOIN retrieval.source_nodes n ON n.id=s.ancestor_id
 LEFT JOIN retrieval.source_heads h ON h.source_id=n.id WHERE s.source_id IN(
 SELECT id FROM retrieval.source_nodes WHERE document_version_id IN(
 SELECT document_version_id FROM design.bid_package_members WHERE revision_id IN(target,authority)))
$$;
CREATE FUNCTION bid_requirement_context_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,design,retrieval AS $$
DECLARE inv design.bid_package_requirement_inventories; own record; authority record; current_identity jsonb; sources jsonb; item jsonb;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'requirement_requires_read_committed' USING ERRCODE='25001'; END IF;
 PERFORM id FROM design.bids WHERE id=NEW.bid_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(90127,1);
 IF TG_TABLE_NAME='bid_package_requirement_inventories' THEN inv=NEW;
 ELSE
  SELECT * INTO inv FROM design.bid_package_requirement_inventories WHERE id=NEW.inventory_id AND bid_id=NEW.bid_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'requirement_inventory_missing' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM design.bid_package_requirement_heads WHERE target_revision_id=inv.target_revision_id AND inventory_id=inv.id) THEN RAISE EXCEPTION 'requirement_inventory_stale' USING ERRCODE='23514'; END IF;
  IF (NEW.manifest->>'inventory_digest' IS DISTINCT FROM inv.digest::text) OR (NEW.manifest->>'package_digest' IS DISTINCT FROM inv.manifest->>'package_digest') THEN RAISE EXCEPTION 'requirement_attempt_stale' USING ERRCODE='23514'; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM design.bid_profiles WHERE bid_id=NEW.bid_id AND (archived_at IS NOT NULL OR phase IN('closed','terminated'))) THEN RAISE EXCEPTION 'requirement_project_closed' USING ERRCODE='23514'; END IF;
 SELECT r.*,p.kind,p.bid_id INTO own FROM design.bid_package_revisions r JOIN design.bid_packages p ON p.id=r.package_id WHERE r.id=inv.target_revision_id;
 SELECT r.*,p.kind,p.bid_id INTO authority FROM design.bid_package_revisions r JOIN design.bid_packages p ON p.id=r.package_id WHERE r.id=inv.authority_revision_id;
 IF own.id IS NULL OR authority.id IS NULL OR own.kind<>'bid' OR authority.kind<>'authority' OR own.bid_id<>NEW.bid_id OR authority.bid_id<>NEW.bid_id OR own.manifest->'scope' IS DISTINCT FROM authority.manifest->'scope' THEN RAISE EXCEPTION 'requirement_package_scope_mismatch' USING ERRCODE='23514'; END IF;
 PERFORM package_id FROM design.bid_package_heads WHERE package_id IN(own.package_id,authority.package_id) ORDER BY package_id FOR SHARE;
 IF NOT EXISTS(SELECT 1 FROM design.bid_package_heads WHERE package_id=own.package_id AND revision_id=own.id) OR NOT EXISTS(SELECT 1 FROM design.bid_package_heads WHERE package_id=authority.package_id AND revision_id=authority.id) THEN RAISE EXCEPTION 'requirement_package_stale' USING ERRCODE='23514'; END IF;
 current_identity=design.identity_snapshot_v2(NEW.bid_id);
 IF current_identity->>'digest' IS DISTINCT FROM own.identity_digest::text OR current_identity->>'digest' IS DISTINCT FROM authority.identity_digest::text OR inv.manifest->>'identity_digest' IS DISTINCT FROM current_identity->>'digest' THEN RAISE EXCEPTION 'requirement_identity_stale' USING ERRCODE='23514'; END IF;
 IF inv.manifest->>'package_digest' IS DISTINCT FROM own.digest::text OR inv.manifest->>'authority_digest' IS DISTINCT FROM authority.digest::text OR NOT EXISTS(SELECT 1 FROM design.bid_profiles WHERE bid_id=NEW.bid_id AND current_rule_set_id=inv.rule_set_id) OR NOT EXISTS(SELECT 1 FROM design.rule_sets WHERE id=inv.rule_set_id AND digest::text=inv.manifest->>'rule_set_digest') THEN RAISE EXCEPTION 'requirement_rule_or_package_stale' USING ERRCODE='23514'; END IF;
 sources=design.bid_requirement_source_snapshot(own.id,authority.id);
 IF sources IS DISTINCT FROM inv.manifest->'source_snapshot' THEN RAISE EXCEPTION 'requirement_source_snapshot_stale' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(sources) LOOP
  IF item->>'policy_class' IS DISTINCT FROM 'prebid_content' OR item->>'status' IS DISTINCT FROM 'published' OR (item->>'known_at')::timestamptz>clock_timestamp() OR (item->>'valid_from')::timestamptz>clock_timestamp() OR (item->>'valid_from') IS NULL OR (item->>'valid_until')::timestamptz<=clock_timestamp() THEN RAISE EXCEPTION 'requirement_source_not_effective' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER inventory_context BEFORE INSERT ON bid_package_requirement_inventories FOR EACH ROW EXECUTE FUNCTION bid_requirement_context_guard();
CREATE TRIGGER attempt_context BEFORE INSERT ON bid_package_coverage_attempts FOR EACH ROW EXECUTE FUNCTION bid_requirement_context_guard();

CREATE FUNCTION bid_requirement_shape(value jsonb) RETURNS boolean LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,design AS $$
DECLARE key text; item jsonb;
BEGIN
 IF NOT package_json_keys(value,ARRAY['requirement_id','scope','origin','source_refs','categories','target_part_ids','minimum_count','applicability','applicability_evidence','valid_at','conditions','checks','identity_version']) OR NOT package_json_text(value->'requirement_id') OR (value->>'origin' IN('authority','company','manual')) IS NOT TRUE OR (value->>'applicability' IN('applicable','unknown','not_applicable')) IS NOT TRUE OR value->>'identity_version' IS DISTINCT FROM 'bid-requirement/1' THEN RETURN false; END IF;
 FOREACH key IN ARRAY ARRAY['source_refs','categories','target_part_ids','applicability_evidence','conditions','checks'] LOOP
  IF jsonb_typeof(value->key) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(value->key) v WHERE NOT package_json_text(v)) OR (SELECT count(*) FROM jsonb_array_elements(value->key))<>(SELECT count(DISTINCT v) FROM jsonb_array_elements(value->key) v) THEN RETURN false; END IF;
 END LOOP;
 IF jsonb_array_length(value->'source_refs')=0 OR jsonb_array_length(value->'categories')=0 OR (value->'minimum_count' IS DISTINCT FROM 'null'::jsonb AND NOT package_json_integer(value->'minimum_count',0)) THEN RETURN false; END IF;
 IF value->>'applicability'='not_applicable' AND jsonb_array_length(value->'applicability_evidence')=0 THEN RETURN false; END IF;
 IF value->'valid_at' IS DISTINCT FROM 'null'::jsonb THEN
  IF jsonb_typeof(value->'valid_at') IS DISTINCT FROM 'string' OR (value->>'valid_at' ~ '^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d+)?(Z|[+-]([01]\d|2[0-3]):[0-5]\d)$') IS NOT TRUE THEN RETURN false; END IF;
  PERFORM (value->>'valid_at')::timestamptz;
 END IF;
 RETURN true;
EXCEPTION WHEN datetime_field_overflow OR invalid_datetime_format THEN RETURN false;
END $$;

CREATE FUNCTION bid_requirement_binding_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,design AS $$
DECLARE inv design.bid_package_requirement_inventories; rule design.rule_items; snapshot jsonb; decision jsonb; current_id uuid; req jsonb; evidence text; bound boolean; key text; expected_conditions jsonb='[]'; expected_refs jsonb; value jsonb; ordinal bigint;
BEGIN
 SELECT * INTO inv FROM design.bid_package_requirement_inventories WHERE id=NEW.inventory_id;
 IF NEW.payload IS DISTINCT FROM inv.manifest->'requirements'->NEW.ordinal THEN RAISE EXCEPTION 'requirement_manifest_binding_mismatch' USING ERRCODE='23514'; END IF;
 SELECT * INTO rule FROM design.rule_items WHERE id=NEW.rule_id AND bid_id=NEW.bid_id AND rule_set_id=inv.rule_set_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'requirement_rule_binding_mismatch' USING ERRCODE='23514'; END IF;
 snapshot=to_jsonb(rule)||jsonb_build_object('max_points',rule.max_points::text);
 IF snapshot IS DISTINCT FROM NEW.payload->'rule_snapshot' OR NEW.payload->>'rule_digest' IS DISTINCT FROM encode(sha256(convert_to(design.package_canonical_json(snapshot),'UTF8')),'hex') THEN RAISE EXCEPTION 'requirement_rule_snapshot_mismatch' USING ERRCODE='23514'; END IF;
 SELECT coalesce(h.decision_id,co.from_decision_id) INTO current_id FROM design.rule_items r LEFT JOIN design.decision_heads h ON h.bid_id=r.bid_id AND h.object_kind='rule' AND h.object_id=r.id LEFT JOIN design.rule_carryovers co ON co.new_rule_id=r.id WHERE r.id=rule.id;
 SELECT to_jsonb(d) INTO decision FROM design.decisions d WHERE d.id=current_id;
 IF NEW.decision_id IS DISTINCT FROM current_id OR NEW.payload->'decision_snapshot' IS DISTINCT FROM coalesce(decision,'null'::jsonb) THEN RAISE EXCEPTION 'requirement_decision_snapshot_mismatch' USING ERRCODE='23514'; END IF;
 req=NEW.payload->'requirement';
 IF NOT bid_requirement_shape(req) OR req->>'requirement_id' IS DISTINCT FROM NEW.rule_id::text OR req->'scope' IS DISTINCT FROM (SELECT manifest->'scope' FROM design.bid_package_revisions WHERE id=inv.target_revision_id) OR req->>'origin' IS DISTINCT FROM (CASE WHEN rule.origin_kind='model_extracted' THEN 'authority' ELSE 'manual' END) THEN RAISE EXCEPTION 'requirement_projection_shape' USING ERRCODE='23514'; END IF;
 expected_refs=jsonb_build_array('rule:'||rule.id::text||':'||(NEW.payload->>'rule_digest'))||(SELECT coalesce(jsonb_agg('evidence:'||v ORDER BY n),'[]') FROM unnest(rule.evidence_ids) WITH ORDINALITY x(v,n));
 IF req->'source_refs' IS DISTINCT FROM expected_refs THEN RAISE EXCEPTION 'requirement_rule_reference_missing' USING ERRCODE='23514'; END IF;
 FOREACH key IN ARRAY ARRAY['conditions','exceptions'] LOOP
  IF jsonb_typeof(snapshot->key) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'requirement_rule_condition_shape' USING ERRCODE='23514'; END IF;
  FOR value,ordinal IN SELECT v,n FROM jsonb_array_elements(snapshot->key) WITH ORDINALITY x(v,n) LOOP
   IF jsonb_typeof(value) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'requirement_rule_condition_shape' USING ERRCODE='23514'; END IF;
   expected_conditions=expected_conditions||jsonb_build_array(key||':'||(ordinal-1)::text||':'||encode(sha256(convert_to(package_canonical_json(value),'UTF8')),'hex'));
  END LOOP;
 END LOOP;
 IF req->'conditions' IS DISTINCT FROM expected_conditions OR NOT package_json_keys(NEW.payload->'annotation',ARRAY['kind','mapped','reason','basis_evidence_ids']) OR NEW.payload->'annotation'->>'kind' IS DISTINCT FROM 'human_check_plan_declaration' OR jsonb_typeof(NEW.payload->'annotation'->'mapped') IS DISTINCT FROM 'boolean' OR NOT bid_requirement_uuid_array(NEW.payload->'annotation'->'basis_evidence_ids') THEN RAISE EXCEPTION 'requirement_annotation_shape' USING ERRCODE='23514'; END IF;
 IF NEW.payload->'annotation'->'mapped'='true'::jsonb THEN
  IF NOT package_json_text(NEW.payload->'annotation'->'reason') OR jsonb_array_length(NEW.payload->'annotation'->'basis_evidence_ids')=0 THEN RAISE EXCEPTION 'requirement_annotation_evidence_missing' USING ERRCODE='23514'; END IF;
 ELSE
  IF req->'categories'<>'["未分类"]'::jsonb OR req->'target_part_ids'<>'[]'::jsonb OR req->'minimum_count'<>'null'::jsonb OR req->>'applicability'<>'unknown' OR req->'applicability_evidence'<>'[]'::jsonb OR req->'valid_at'<>'null'::jsonb OR req->'checks'<>'[]'::jsonb OR NEW.payload->'annotation'->'reason'<>'null'::jsonb OR NEW.payload->'annotation'->'basis_evidence_ids'<>'[]'::jsonb THEN RAISE EXCEPTION 'requirement_unmapped_must_stay_unknown' USING ERRCODE='23514'; END IF;
 END IF;
 IF req->>'applicability'='not_applicable' AND jsonb_array_length(req->'applicability_evidence')=0 THEN RAISE EXCEPTION 'requirement_exemption_unproven' USING ERRCODE='23514'; END IF;
 SELECT cardinality(rule.evidence_ids)>0 AND count(*)=cardinality(rule.evidence_ids) INTO bound FROM design.evidence_blocks e WHERE e.id=ANY(rule.evidence_ids) AND e.bid_id=NEW.bid_id AND EXISTS(SELECT 1 FROM design.bid_package_members m JOIN design.bid_package_parts p ON p.revision_id=m.revision_id AND p.member_id=m.member_id WHERE m.bid_id=e.bid_id AND m.parse_run_id=e.parse_id AND m.revision_id=inv.authority_revision_id AND e.physical_page BETWEEN p.first_page AND p.last_page);
 IF NEW.payload->'source_bound' IS DISTINCT FROM to_jsonb(bound) THEN RAISE EXCEPTION 'requirement_source_binding_mismatch' USING ERRCODE='23514'; END IF;
 IF (SELECT count(*) FROM design.evidence_blocks WHERE bid_id=NEW.bid_id AND id=ANY(rule.evidence_ids))<>cardinality(rule.evidence_ids) THEN RAISE EXCEPTION 'requirement_rule_evidence_cross_bid' USING ERRCODE='23514'; END IF;
 FOR evidence IN SELECT jsonb_array_elements_text(NEW.payload->'annotation'->'basis_evidence_ids') UNION SELECT replace(jsonb_array_elements_text(req->'applicability_evidence'),'evidence:','') LOOP
  IF NOT EXISTS(SELECT 1 FROM design.evidence_blocks e JOIN design.bid_package_members m ON m.bid_id=e.bid_id AND m.parse_run_id=e.parse_id JOIN design.bid_package_parts p ON p.revision_id=m.revision_id AND p.member_id=m.member_id AND e.physical_page BETWEEN p.first_page AND p.last_page WHERE e.id::text=evidence AND e.bid_id=NEW.bid_id AND m.revision_id=inv.authority_revision_id) THEN RAISE EXCEPTION 'requirement_evidence_outside_authority' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(req->'target_part_ids') id WHERE NOT EXISTS(SELECT 1 FROM design.bid_package_parts p WHERE p.revision_id=inv.target_revision_id AND p.part_id=id AND req->'categories' ? p.category)) THEN RAISE EXCEPTION 'requirement_target_scope_mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER requirement_binding BEFORE INSERT ON bid_package_requirements FOR EACH ROW EXECUTE FUNCTION bid_requirement_binding_guard();

CREATE FUNCTION bid_requirement_result_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,design AS $$
DECLARE attempt design.bid_package_coverage_attempts; inv design.bid_package_requirement_inventories; binding design.bid_package_requirements; value jsonb; req jsonb; inspected jsonb; part record; evidence text; state text; declared int8multirange;
BEGIN
 SELECT * INTO attempt FROM design.bid_package_coverage_attempts WHERE id=NEW.attempt_id;
 SELECT * INTO inv FROM design.bid_package_requirement_inventories WHERE id=NEW.inventory_id;
 SELECT * INTO binding FROM design.bid_package_requirements WHERE inventory_id=NEW.inventory_id AND rule_id=NEW.rule_id;
 IF NEW.payload IS DISTINCT FROM attempt.manifest->'results'->NEW.ordinal THEN RAISE EXCEPTION 'requirement_result_manifest_mismatch' USING ERRCODE='23514'; END IF;
 value=NEW.payload->'coverage'; req=binding.payload->'requirement'; state=value->>'state';
 IF NOT package_json_keys(value,ARRAY['requirement_id','package_digest','requirement_digest','state','inspected','evidence_refs','reason']) OR value->>'package_digest' IS DISTINCT FROM inv.manifest->>'package_digest' OR (state IN('not_provided','missing','pending_parse','parse_gap','unsupported','verified','not_applicable','conflict','unknown')) IS NOT TRUE OR jsonb_typeof(value->'inspected') IS DISTINCT FROM 'array' OR jsonb_typeof(value->'evidence_refs') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'requirement_result_shape' USING ERRCODE='23514'; END IF;
 IF state IN('verified','not_applicable') THEN
  IF NEW.payload->>'basis_type'<>'human_scope_declaration' OR jsonb_array_length(NEW.payload->'evidence_ids')=0 OR NOT package_json_text(value->'reason') THEN RAISE EXCEPTION 'requirement_human_declaration_missing' USING ERRCODE='23514'; END IF;
  IF binding.payload->'source_bound' IS DISTINCT FROM 'true'::jsonb OR binding.payload->'decision_snapshot'->>'decision' IS DISTINCT FROM 'confirmed' OR binding.payload->'rule_snapshot'->>'provenance_status' IS DISTINCT FROM 'effective' THEN RAISE EXCEPTION 'requirement_not_confirmed' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(inv.manifest->'source_snapshot')) OR EXISTS(SELECT 1 FROM jsonb_array_elements(inv.manifest->'source_snapshot') s WHERE s->>'status' IS DISTINCT FROM 'published' OR (s->>'known_at')::timestamptz>clock_timestamp() OR (s->>'valid_from')::timestamptz>clock_timestamp() OR (s->>'valid_until')::timestamptz<=clock_timestamp()) THEN RAISE EXCEPTION 'requirement_source_unproven' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM design.bid_package_members m WHERE m.revision_id IN(inv.target_revision_id,inv.authority_revision_id) AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(inv.manifest->'source_snapshot') s WHERE s->>'document_version_id'=m.document_version_id::text)) THEN RAISE EXCEPTION 'requirement_source_unproven' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.payload->>'basis_type' IS DISTINCT FROM 'human_observation' THEN RAISE EXCEPTION 'requirement_observation_required' USING ERRCODE='23514'; END IF;
  IF NOT package_json_text(value->'reason') THEN RAISE EXCEPTION 'requirement_result_reason_missing' USING ERRCODE='23514'; END IF;
 END IF;
 FOR inspected IN SELECT * FROM jsonb_array_elements(value->'inspected') LOOP
  IF NOT package_json_keys(inspected,ARRAY['part_id','first_page','last_page']) OR NOT package_json_text(inspected->'part_id') OR NOT package_json_integer(inspected->'first_page',1) OR NOT package_json_integer(inspected->'last_page',1) THEN RAISE EXCEPTION 'requirement_inspected_shape' USING ERRCODE='23514'; END IF;
  SELECT * INTO part FROM design.bid_package_parts p WHERE revision_id=inv.target_revision_id AND part_id=inspected->>'part_id';
  IF NOT FOUND OR NOT (req->'categories' ? part.category) OR (jsonb_array_length(req->'target_part_ids')>0 AND NOT(req->'target_part_ids' ? part.part_id)) OR (inspected->>'first_page')::bigint<part.first_page OR (inspected->>'last_page')::bigint>part.last_page OR (inspected->>'last_page')::bigint<(inspected->>'first_page')::bigint THEN RAISE EXCEPTION 'requirement_inspected_scope_mismatch' USING ERRCODE='23514'; END IF;
  IF state='verified' AND NOT EXISTS(SELECT 1 FROM design.bid_package_members m WHERE m.revision_id=inv.target_revision_id AND m.member_id=part.member_id AND m.payload->'parse'->>'status'='frozen') THEN RAISE EXCEPTION 'requirement_parse_not_frozen' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF state='verified' THEN
  IF req->>'applicability' IS DISTINCT FROM 'applicable' OR (req->'minimum_count'<>'null'::jsonb AND NOT(req->'checks' ? 'quantity')) THEN RAISE EXCEPTION 'requirement_applicability_or_quantity_unknown' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM design.bid_package_parts p WHERE p.revision_id=inv.target_revision_id AND req->'categories' ? p.category AND (jsonb_array_length(req->'target_part_ids')=0 OR req->'target_part_ids' ? p.part_id)) THEN RAISE EXCEPTION 'requirement_verified_target_empty' USING ERRCODE='23514'; END IF;
  FOR part IN SELECT * FROM design.bid_package_parts p WHERE p.revision_id=inv.target_revision_id AND req->'categories' ? p.category AND (jsonb_array_length(req->'target_part_ids')=0 OR req->'target_part_ids' ? p.part_id) LOOP
   SELECT range_agg(int8range((v->>'first_page')::bigint,(v->>'last_page')::bigint,'[]')) INTO declared FROM jsonb_array_elements(value->'inspected') v WHERE v->>'part_id'=part.part_id;
   IF (declared @> int8range(part.first_page,part.last_page,'[]')) IS NOT TRUE THEN RAISE EXCEPTION 'requirement_verified_range_incomplete' USING ERRCODE='23514'; END IF;
  END LOOP;
 ELSIF state='not_applicable' AND (req->>'applicability' IS DISTINCT FROM 'not_applicable' OR jsonb_array_length(req->'applicability_evidence')=0) THEN RAISE EXCEPTION 'requirement_exemption_unproven' USING ERRCODE='23514'; END IF;
 IF (SELECT coalesce(jsonb_agg('evidence:'||v ORDER BY ordinal),'[]'::jsonb) FROM jsonb_array_elements_text(NEW.payload->'evidence_ids') WITH ORDINALITY x(v,ordinal)) IS DISTINCT FROM value->'evidence_refs' THEN RAISE EXCEPTION 'requirement_result_evidence_binding' USING ERRCODE='23514'; END IF;
 FOR evidence IN SELECT * FROM jsonb_array_elements_text(NEW.payload->'evidence_ids') LOOP
  IF NOT EXISTS(SELECT 1 FROM design.evidence_blocks e JOIN design.bid_package_members m ON m.bid_id=e.bid_id AND m.parse_run_id=e.parse_id JOIN design.bid_package_parts p ON p.revision_id=m.revision_id AND p.member_id=m.member_id AND e.physical_page BETWEEN p.first_page AND p.last_page WHERE e.id::text=evidence AND e.bid_id=inv.bid_id AND ((state='verified' AND m.revision_id=inv.target_revision_id AND EXISTS(SELECT 1 FROM jsonb_array_elements(value->'inspected') r WHERE r->>'part_id'=p.part_id AND e.physical_page BETWEEN (r->>'first_page')::bigint AND (r->>'last_page')::bigint)) OR (state<>'verified' AND m.revision_id IN(inv.target_revision_id,inv.authority_revision_id)))) THEN RAISE EXCEPTION 'requirement_result_evidence_outside_frozen_scope' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER coverage_result_binding BEFORE INSERT ON bid_package_coverage_results FOR EACH ROW EXECUTE FUNCTION bid_requirement_result_guard();

CREATE FUNCTION bid_requirement_head_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,design AS $$
DECLARE row design.bid_package_requirement_inventories;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'requirement_head_delete_forbidden' USING ERRCODE='23514'; END IF;
 SELECT * INTO row FROM design.bid_package_requirement_inventories WHERE id=NEW.inventory_id;
 IF row.target_revision_id IS DISTINCT FROM NEW.target_revision_id OR row.revision IS DISTINCT FROM NEW.revision OR row.digest IS DISTINCT FROM NEW.digest OR (TG_OP='INSERT' AND row.revision<>1) OR (TG_OP='UPDATE' AND (OLD.target_revision_id<>NEW.target_revision_id OR row.revision<>OLD.revision+1 OR row.predecessor_digest<>OLD.digest)) THEN RAISE EXCEPTION 'requirement_head_cas_mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER requirement_head_forward BEFORE INSERT OR UPDATE OR DELETE ON bid_package_requirement_heads FOR EACH ROW EXECUTE FUNCTION bid_requirement_head_guard();

-- This is the final authority check, called by initially-deferred constraints at commit.
-- All changing authorities are locked before choosing one shared wall-clock instant.
CREATE FUNCTION bid_requirement_final_recheck(inventory uuid) RETURNS void LANGUAGE plpgsql
 SET search_path=pg_catalog,design,retrieval AS $$
DECLARE inv design.bid_package_requirement_inventories; own design.bid_package_revisions; authority design.bid_package_revisions;
 current_identity jsonb; snapshot jsonb; item jsonb; binding record; current_id uuid; current_snapshot jsonb; check_at timestamptz;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'requirement_requires_read_committed' USING ERRCODE='25001'; END IF;
 SELECT * INTO inv FROM design.bid_package_requirement_inventories WHERE id=inventory;
 IF NOT FOUND THEN RAISE EXCEPTION 'requirement_inventory_missing' USING ERRCODE='23514'; END IF;
 PERFORM id FROM design.bids WHERE id=inv.bid_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(90127,1);
 SELECT * INTO own FROM design.bid_package_revisions WHERE id=inv.target_revision_id;
 SELECT * INTO authority FROM design.bid_package_revisions WHERE id=inv.authority_revision_id;
 PERFORM package_id FROM design.bid_package_heads WHERE package_id IN(own.package_id,authority.package_id) ORDER BY package_id FOR SHARE;
 PERFORM object_id FROM design.decision_heads WHERE bid_id=inv.bid_id AND object_kind='rule'
 AND object_id IN(SELECT rule_id FROM design.bid_package_requirements WHERE inventory_id=inv.id) ORDER BY object_id FOR SHARE;
 PERFORM source_id FROM retrieval.source_heads WHERE source_id IN(
 SELECT s.ancestor_id FROM retrieval.source_ancestors s JOIN retrieval.source_nodes n ON n.id=s.source_id
 WHERE n.document_version_id IN(SELECT document_version_id FROM design.bid_package_members WHERE revision_id IN(inv.target_revision_id,inv.authority_revision_id))) ORDER BY source_id FOR SHARE;
 IF NOT EXISTS(SELECT 1 FROM design.bid_profiles WHERE bid_id=inv.bid_id AND current_rule_set_id=inv.rule_set_id AND archived_at IS NULL AND phase NOT IN('closed','terminated')) THEN RAISE EXCEPTION 'requirement_rule_or_project_stale' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM design.bid_package_heads WHERE package_id=own.package_id AND revision_id=own.id) OR NOT EXISTS(SELECT 1 FROM design.bid_package_heads WHERE package_id=authority.package_id AND revision_id=authority.id) THEN RAISE EXCEPTION 'requirement_package_stale' USING ERRCODE='23514'; END IF;
 current_identity=design.identity_snapshot_v2(inv.bid_id);
 IF current_identity->>'digest' IS DISTINCT FROM inv.manifest->>'identity_digest' OR current_identity->>'digest' IS DISTINCT FROM own.identity_digest::text OR current_identity->>'digest' IS DISTINCT FROM authority.identity_digest::text THEN RAISE EXCEPTION 'requirement_identity_stale' USING ERRCODE='23514'; END IF;
 FOR binding IN SELECT * FROM design.bid_package_requirements WHERE inventory_id=inv.id ORDER BY ordinal LOOP
  SELECT coalesce(h.decision_id,co.from_decision_id) INTO current_id FROM design.rule_items r
  LEFT JOIN design.decision_heads h ON h.bid_id=r.bid_id AND h.object_kind='rule' AND h.object_id=r.id
  LEFT JOIN design.rule_carryovers co ON co.new_rule_id=r.id WHERE r.id=binding.rule_id;
  SELECT to_jsonb(d) INTO current_snapshot FROM design.decisions d WHERE id=current_id;
  IF current_id IS DISTINCT FROM binding.decision_id OR coalesce(current_snapshot,'null'::jsonb) IS DISTINCT FROM binding.payload->'decision_snapshot' THEN RAISE EXCEPTION 'requirement_decision_stale' USING ERRCODE='23514'; END IF;
 END LOOP;
 snapshot=design.bid_requirement_source_snapshot(inv.target_revision_id,inv.authority_revision_id);
 IF snapshot IS DISTINCT FROM inv.manifest->'source_snapshot' THEN RAISE EXCEPTION 'requirement_source_snapshot_stale' USING ERRCODE='23514'; END IF;
 check_at=clock_timestamp();
 FOR item IN SELECT value FROM jsonb_array_elements(snapshot) LOOP
  IF item->>'policy_class' IS DISTINCT FROM 'prebid_content' OR item->>'status' IS DISTINCT FROM 'published'
  OR item->>'known_at' IS NULL OR item->>'valid_from' IS NULL
  OR (item->>'known_at')::timestamptz>check_at OR (item->>'valid_from')::timestamptz>check_at
  OR (item->>'valid_until')::timestamptz<=check_at THEN RAISE EXCEPTION 'requirement_source_not_effective' USING ERRCODE='23514'; END IF;
 END LOOP;
END $$;

-- Rebuild every assessment field from immutable bindings and actual result rows.
-- In particular, an omitted requirement always remains unknown, never verified.
CREATE FUNCTION bid_requirement_assessment(attempt uuid) RETURNS jsonb LANGUAGE plpgsql
 SET search_path=pg_catalog,design AS $$
DECLARE a design.bid_package_coverage_attempts; inv design.bid_package_requirement_inventories;
 states jsonb; identities jsonb; inspected jsonb; gaps jsonb;
BEGIN
 SELECT * INTO a FROM design.bid_package_coverage_attempts WHERE id=attempt;
 SELECT * INTO inv FROM design.bid_package_requirement_inventories WHERE id=a.inventory_id;
 SELECT coalesce(jsonb_object_agg(q.rule_id::text,coalesce(r.payload->'coverage'->>'state','unknown')),'{}'::jsonb),
 coalesce(jsonb_object_agg(q.rule_id::text,q.requirement_digest::text),'{}'::jsonb) INTO states,identities
 FROM design.bid_package_requirements q LEFT JOIN design.bid_package_coverage_results r ON r.inventory_id=q.inventory_id AND r.rule_id=q.rule_id AND r.attempt_id=a.id WHERE q.inventory_id=inv.id;
 SELECT coalesce(jsonb_agg(jsonb_build_object('requirement_id',r.rule_id,'requirement_digest',r.requirement_digest,
 'document_version_id',m.document_version_id,'part_id',v->>'part_id','first_page',v->'first_page','last_page',v->'last_page') ORDER BY r.ordinal,n),'[]'::jsonb)
 INTO inspected FROM design.bid_package_coverage_results r
 CROSS JOIN LATERAL jsonb_array_elements(r.payload->'coverage'->'inspected') WITH ORDINALITY x(v,n)
 JOIN design.bid_package_parts p ON p.revision_id=inv.target_revision_id AND p.part_id=v->>'part_id'
 JOIN design.bid_package_members m ON m.revision_id=p.revision_id AND m.member_id=p.member_id WHERE r.attempt_id=a.id;
 SELECT coalesce(jsonb_agg(jsonb_build_object('member_id',v->'member_id','document_version_id',v->'document_version_id',
 'purposes',v->'purposes','codes',
 (CASE WHEN v->'page_count'='null'::jsonb THEN '["page_count_unknown"]'::jsonb ELSE '[]'::jsonb END)||
 (CASE WHEN jsonb_array_length(v->'parts')=0 THEN '["parts_unassigned"]'::jsonb ELSE '[]'::jsonb END)||
 (CASE WHEN v->'parse'->>'status'<>'frozen' THEN '["parse_identity_not_frozen"]'::jsonb ELSE '[]'::jsonb END),
 'reason',v->'parse'->'reason') ORDER BY n),'[]'::jsonb) INTO gaps
 FROM design.bid_package_revisions p CROSS JOIN LATERAL jsonb_array_elements(p.manifest->'members') WITH ORDINALITY x(v,n)
 WHERE p.id=inv.target_revision_id AND (v->'page_count'='null'::jsonb OR jsonb_array_length(v->'parts')=0 OR v->'parse'->>'status'<>'frozen');
 RETURN jsonb_build_object('package_digest',inv.manifest->'package_digest','complete',false,'requirement_inventory_complete',false,
 'requirement_states',states,'requirement_digests',identities,'inspected_ranges',inspected,'member_gaps',gaps);
END $$;

CREATE FUNCTION bid_requirement_complete_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,design AS $$
DECLARE actual bigint; expected bigint;
BEGIN
 IF TG_TABLE_NAME='bid_package_requirement_inventories' THEN
  SELECT count(*) INTO actual FROM design.bid_package_requirements WHERE inventory_id=NEW.id;
  expected=jsonb_array_length(NEW.manifest->'requirements');
  IF actual IS DISTINCT FROM expected OR actual IS DISTINCT FROM (SELECT count(*) FROM design.rule_items WHERE rule_set_id=NEW.rule_set_id) THEN RAISE EXCEPTION 'requirement_inventory_incomplete' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM design.bid_package_requirement_heads WHERE target_revision_id=NEW.target_revision_id AND revision>=NEW.revision) THEN RAISE EXCEPTION 'requirement_head_missing' USING ERRCODE='23514'; END IF;
  PERFORM design.bid_requirement_final_recheck(NEW.id);
 ELSE
  SELECT count(*) INTO actual FROM design.bid_package_coverage_results WHERE attempt_id=NEW.id;
  expected=jsonb_array_length(NEW.manifest->'results');
  IF actual IS DISTINCT FROM expected THEN RAISE EXCEPTION 'requirement_attempt_incomplete' USING ERRCODE='23514'; END IF;
  IF NOT EXISTS(SELECT 1 FROM design.bid_package_requirement_heads h JOIN design.bid_package_requirement_inventories i ON i.id=NEW.inventory_id WHERE h.target_revision_id=i.target_revision_id AND h.inventory_id=NEW.inventory_id) THEN RAISE EXCEPTION 'requirement_inventory_stale' USING ERRCODE='23514'; END IF;
  IF NEW.manifest->'assessment' IS DISTINCT FROM design.bid_requirement_assessment(NEW.id) THEN RAISE EXCEPTION 'requirement_assessment_mismatch' USING ERRCODE='23514'; END IF;
  PERFORM design.bid_requirement_final_recheck(NEW.inventory_id);
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER inventory_complete AFTER INSERT ON bid_package_requirement_inventories DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_requirement_complete_guard();
CREATE CONSTRAINT TRIGGER attempt_complete AFTER INSERT ON bid_package_coverage_attempts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bid_requirement_complete_guard();
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['bid_package_requirement_inventories','bid_package_requirements','bid_package_coverage_attempts','bid_package_coverage_results'] LOOP
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON design.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',name);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION bid_requirement_envelope_shape(jsonb),bid_requirement_uuid_array(jsonb),bid_requirement_decision_serial_guard(),bid_requirement_carryover_serial_guard(),bid_requirement_source_serial_guard(),bid_requirement_source_snapshot(uuid,uuid),bid_requirement_context_guard(),bid_requirement_shape(jsonb),bid_requirement_binding_guard(),bid_requirement_result_guard(),bid_requirement_head_guard(),bid_requirement_final_recheck(uuid),bid_requirement_assessment(uuid),bid_requirement_complete_guard() FROM PUBLIC;
COMMIT;
