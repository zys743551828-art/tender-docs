-- 新系统独立初始化增补。不得对旧release或机制库热补/临时授权。
-- 只有API创建可信冻结映射；研究Worker只通过已拥有执行权的operation取得bool/摘要。
BEGIN;
CREATE TABLE design.research_chapter_authorities(
 chapter_package_id uuid PRIMARY KEY REFERENCES design.chapter_packages(id),
 bid_id uuid NOT NULL REFERENCES design.bids(id),
 as_of timestamptz NOT NULL,
 bindings jsonb NOT NULL CHECK(jsonb_typeof(bindings)='array' AND jsonb_array_length(bindings)>0),
 ancestor_heads jsonb NOT NULL CHECK(jsonb_typeof(ancestor_heads)='array' AND jsonb_array_length(ancestor_heads)>0),
 authority_digest design.sha256 NOT NULL,
 created_by uuid NOT NULL REFERENCES design.members(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE design.research_preview_chapters(
 preview_id uuid NOT NULL REFERENCES design.experiment_previews(id),
 sample_id uuid NOT NULL REFERENCES design.samples(id),
 chapter_package_id uuid NOT NULL REFERENCES design.research_chapter_authorities(chapter_package_id),
 PRIMARY KEY(preview_id,sample_id,chapter_package_id)
);
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.research_chapter_authorities
 FOR EACH ROW EXECUTE FUNCTION design.append_only();
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.research_preview_chapters
 FOR EACH ROW EXECUTE FUNCTION design.append_only();
REVOKE ALL ON design.research_chapter_authorities,design.research_preview_chapters FROM PUBLIC,tender_worker,tender_research_worker,tender_score_read,tender_research_read;
GRANT SELECT,INSERT ON design.research_chapter_authorities,design.research_preview_chapters TO tender_api;

-- API冻结时使用；返回身份/head/时效，不复制任何正文或官方标签。
CREATE FUNCTION design.capture_research_source_heads(source_ids uuid[]) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog,design,retrieval,pg_temp AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object(
  'source_id',a.source_id,'id',n.id,'document_version_id',n.document_version_id,
  'kind',n.kind,'policy_class',n.policy_class,'known_at',n.known_at,'digest',n.digest,
  'revision',h.revision,'status',h.status,'valid_from',h.valid_from,'valid_until',h.valid_until
 ) ORDER BY a.source_id,n.id),'[]'::jsonb)
 FROM retrieval.source_ancestors a JOIN retrieval.source_nodes n ON n.id=a.ancestor_id
 LEFT JOIN retrieval.source_heads h ON h.source_id=n.id WHERE a.source_id=ANY(source_ids);
$$;
REVOKE ALL ON FUNCTION design.capture_research_source_heads(uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION design.capture_research_source_heads(uuid[]) TO tender_api;

CREATE FUNCTION design.qualify_research_chapter(
 target_operation uuid,target_step text,target_input design.sha256,
 expected_generation bigint,expected_fence bigint,expected_owner uuid,target_chapter uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,design,retrieval,pg_temp AS $$
DECLARE ctx record; capsule design.research_chapter_authorities; binding jsonb; head jsonb;
 source_ids uuid[]; expected_ids uuid[]; actual_ids uuid[]; selected_ids uuid[];
 node record; parent record; block_parse record; known_count integer;
 member record; verdict jsonb; aggregate_proofs jsonb:='[]'::jsonb;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RETURN jsonb_build_object('qualified',false,'reason','fresh_read_committed_required');
 END IF;
 -- 图锁只保护本次短事务的资格判断；调用端禁止跨HTTP/对象写入持锁。
 PERFORM pg_advisory_xact_lock(90127,1);
 IF target_chapter IS NULL THEN
  FOR member IN SELECT mapping.chapter_package_id
   FROM design.research_run_attempts a JOIN design.research_runs r ON r.id=a.run_id
   JOIN design.research_preview_chapters mapping ON mapping.preview_id=r.preview_id AND mapping.sample_id=a.sample_id
   WHERE a.operation_id=target_operation ORDER BY mapping.chapter_package_id LOOP
   verdict:=design.qualify_research_chapter(target_operation,target_step,target_input,
    expected_generation,expected_fence,expected_owner,member.chapter_package_id);
   IF NOT coalesce((verdict->>'qualified')::boolean,false) THEN RETURN verdict; END IF;
   aggregate_proofs:=aggregate_proofs||jsonb_build_array(verdict->>'authority_digest');
  END LOOP;
  IF jsonb_array_length(aggregate_proofs)=0 THEN RETURN jsonb_build_object('qualified',false,'reason','no_frozen_chapters'); END IF;
  RETURN jsonb_build_object('qualified',true,'authority_digest',encode(sha256(convert_to(aggregate_proofs::text,'UTF8')),'hex'));
 END IF;
 SELECT o.bid_id,a.run_id,a.sample_id,r.preview_id,s.submission_id,s.outcome_id,
  sr.body_version_id,sr.review_id,i.material_snapshot_id,i.as_of,
  (i.manifest->>'rule_set_id')::uuid AS rule_set_id,
  cp.parse_id,cp.rule_id,cp.digest AS package_digest,ri.evidence_ids AS rule_evidence
 INTO ctx
 FROM design.operations o
 JOIN design.research_run_attempts a ON a.operation_id=o.id AND a.bid_id=o.bid_id
 JOIN design.research_runs r ON r.id=a.run_id
 JOIN design.research_run_items item ON item.run_id=r.id AND item.sample_id=a.sample_id
  AND item.repeat_index=a.repeat_index AND item.operation_id=o.id AND item.state='running'
 JOIN design.samples s ON s.id=a.sample_id AND s.bid_id=o.bid_id AND s.admission='admitted'
 JOIN design.submission_records sr ON sr.id=s.submission_id AND sr.bid_id=o.bid_id
 JOIN design.submission_heads sh ON sh.bid_id=sr.bid_id AND sh.submission_id=sr.id
 JOIN design.outcome_heads oh ON oh.submission_id=sr.id AND oh.outcome_id=s.outcome_id
 JOIN design.review_runs rr ON rr.id=sr.review_id AND rr.bid_id=o.bid_id
 JOIN design.review_inputs i ON i.id=rr.input_id AND i.bid_id=o.bid_id AND i.body_version_id=sr.body_version_id
 JOIN design.chapter_packages cp ON cp.id=target_chapter AND cp.bid_id=o.bid_id AND cp.review_id=rr.id
 JOIN design.rule_items ri ON ri.id=cp.rule_id AND ri.bid_id=o.bid_id AND ri.kind='scoring'
  AND ri.rule_set_id=(i.manifest->>'rule_set_id')::uuid
 JOIN design.research_preview_chapters mapping ON mapping.preview_id=r.preview_id
  AND mapping.sample_id=s.id AND mapping.chapter_package_id=cp.id
 WHERE o.id=target_operation AND o.kind='research-judgment' AND o.handler_version='research/1'
  AND o.state='running' AND o.cancel_requested_at IS NULL
  AND r.state='running' AND r.cancel_requested_at IS NULL;
 IF NOT FOUND THEN RETURN jsonb_build_object('qualified',false,'reason','execution_or_frozen_scope_invalid'); END IF;
 IF NOT EXISTS(SELECT 1 FROM design.operation_steps p JOIN design.step_receipts receipt
   ON receipt.operation_id=p.operation_id AND receipt.step_key=p.step_key AND receipt.input_digest=p.input_digest
   WHERE p.operation_id=target_operation AND p.step_key=target_step AND p.input_digest=target_input
   AND p.dispatch_generation=expected_generation AND receipt.state='running'
   AND receipt.fence=expected_fence AND receipt.owner_token=expected_owner
   AND receipt.lease_until>clock_timestamp()) THEN
  RETURN jsonb_build_object('qualified',false,'reason','execution_fence_invalid');
 END IF;
 SELECT * INTO capsule FROM design.research_chapter_authorities WHERE chapter_package_id=target_chapter;
 IF NOT FOUND OR capsule.bid_id IS DISTINCT FROM ctx.bid_id OR capsule.as_of IS DISTINCT FROM ctx.as_of
  OR capsule.authority_digest IS DISTINCT FROM encode(sha256(convert_to(jsonb_build_object(
   'bindings',capsule.bindings,'ancestor_heads',capsule.ancestor_heads)::text,'UTF8')),'hex') THEN
  RETURN jsonb_build_object('qualified',false,'reason','frozen_authority_invalid');
 END IF;
 IF NOT EXISTS(SELECT 1 FROM design.parse_runs p JOIN design.document_versions v ON v.id=p.document_version_id
   WHERE p.id=ctx.parse_id AND p.bid_id=ctx.bid_id AND p.document_version_id=ctx.body_version_id
   AND v.artifact_kind='bid_body' AND p.coverage='complete' AND p.gaps='[]'::jsonb
   AND p.id=(SELECT latest.id FROM design.parse_runs latest WHERE latest.document_version_id=p.document_version_id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
   AND p.page_count>0 AND p.page_count=(SELECT count(*) FROM design.pages page WHERE page.parse_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM design.pages page WHERE page.parse_id=p.id AND (page.quality<>'usable' OR page.gaps<>'[]'::jsonb))) THEN
  RETURN jsonb_build_object('qualified',false,'reason','full_body_coverage_changed');
 END IF;
 SELECT array_agg(id ORDER BY id) INTO expected_ids FROM (
  SELECT e.id FROM design.evidence_blocks e WHERE e.bid_id=ctx.bid_id AND e.parse_id=ctx.parse_id
  UNION SELECT unnest(ctx.rule_evidence)
 ) expected;
 SELECT array_agg(DISTINCT entry::uuid ORDER BY entry::uuid),array_agg(DISTINCT (b->>'source_id')::uuid ORDER BY (b->>'source_id')::uuid)
 INTO actual_ids,source_ids FROM jsonb_array_elements(capsule.bindings) b
 CROSS JOIN LATERAL jsonb_array_elements_text(b->'evidence_ids') entry;
 IF expected_ids IS NULL OR actual_ids IS DISTINCT FROM expected_ids
  OR cardinality(source_ids)<>jsonb_array_length(capsule.bindings) THEN
  RETURN jsonb_build_object('qualified',false,'reason','full_evidence_manifest_invalid');
 END IF;
 FOR binding IN SELECT value FROM jsonb_array_elements(capsule.bindings) LOOP
  SELECT p.*,v.artifact_kind,b.digest AS original_digest INTO block_parse
   FROM design.parse_runs p JOIN design.document_versions v ON v.id=p.document_version_id
   JOIN design.blobs b ON b.id=v.blob_id
   WHERE p.id=(binding->>'parse_id')::uuid AND p.bid_id=ctx.bid_id
   AND p.document_version_id=(binding->>'document_version_id')::uuid;
  IF NOT FOUND OR (block_parse.id<>ctx.parse_id AND NOT EXISTS(
   SELECT 1 FROM design.material_assignments m WHERE m.snapshot_id=ctx.material_snapshot_id
    AND m.bid_id=ctx.bid_id AND m.document_version_id=block_parse.document_version_id AND m.role IN ('tender','supplement'))) THEN
   RETURN jsonb_build_object('qualified',false,'reason','evidence_original_scope_invalid');
  END IF;
  SELECT array_agg(e.id ORDER BY e.id) INTO selected_ids FROM design.evidence_blocks e
   WHERE e.id=ANY(expected_ids) AND e.bid_id=ctx.bid_id AND e.parse_id=block_parse.id
   AND EXISTS(SELECT 1 FROM design.pages p WHERE p.bid_id=e.bid_id AND p.parse_id=e.parse_id AND p.physical_page=e.physical_page);
  SELECT array_agg(entry::uuid ORDER BY entry::uuid) INTO actual_ids FROM jsonb_array_elements_text(binding->'evidence_ids') entry;
  IF selected_ids IS NULL OR actual_ids IS DISTINCT FROM selected_ids THEN
   RETURN jsonb_build_object('qualified',false,'reason','exact_parse_evidence_invalid');
  END IF;
  SELECT * INTO node FROM retrieval.source_nodes WHERE id=(binding->>'source_id')::uuid;
  SELECT * INTO parent FROM retrieval.source_nodes WHERE id=(binding->>'original_source_id')::uuid;
  IF node.id IS NULL OR parent.id IS NULL OR node.kind<>'document' OR parent.kind<>'document'
   OR node.document_version_id IS DISTINCT FROM block_parse.document_version_id
   OR parent.document_version_id IS DISTINCT FROM block_parse.document_version_id
   OR node.digest IS DISTINCT FROM block_parse.original_digest OR parent.digest IS DISTINCT FROM block_parse.original_digest
   OR NOT EXISTS(SELECT 1 FROM retrieval.lineage_edges edge WHERE edge.child_id=node.id AND edge.parent_id=parent.id)
   OR NOT EXISTS(SELECT 1 FROM retrieval.source_heads h WHERE h.source_id=node.id AND h.revision=(binding->>'source_revision')::bigint) THEN
   RETURN jsonb_build_object('qualified',false,'reason','canonical_parse_source_invalid');
  END IF;
 END LOOP;
 SELECT count(*) INTO known_count FROM retrieval.source_ancestors a WHERE a.source_id=ANY(source_ids);
 IF known_count<>jsonb_array_length(capsule.ancestor_heads) OR EXISTS(
  SELECT 1 FROM retrieval.source_ancestors a WHERE a.source_id=ANY(source_ids)
  AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(capsule.ancestor_heads) frozen
   WHERE (frozen->>'source_id')::uuid=a.source_id AND (frozen->>'id')::uuid=a.ancestor_id)
 ) THEN RETURN jsonb_build_object('qualified',false,'reason','ancestor_graph_changed'); END IF;
 FOR head IN SELECT value FROM jsonb_array_elements(capsule.ancestor_heads) LOOP
  SELECT n.*,h.revision,h.status,h.valid_from,h.valid_until,v.bid_id AS owner_bid INTO node
   FROM retrieval.source_nodes n LEFT JOIN retrieval.source_heads h ON h.source_id=n.id
   LEFT JOIN design.document_versions v ON v.id=n.document_version_id WHERE n.id=(head->>'id')::uuid;
  IF NOT FOUND OR node.owner_bid IS DISTINCT FROM ctx.bid_id OR node.kind<>'document'
   OR node.policy_class<>'prebid_content' OR node.status IS DISTINCT FROM 'published'
   OR (node.document_version_id IS DISTINCT FROM ctx.body_version_id AND NOT EXISTS(
    SELECT 1 FROM design.material_assignments scope WHERE scope.snapshot_id=ctx.material_snapshot_id
     AND scope.bid_id=ctx.bid_id AND scope.document_version_id=node.document_version_id AND scope.role IN ('tender','supplement')))
   OR node.known_at>ctx.as_of OR node.valid_from>ctx.as_of OR node.valid_until<=ctx.as_of
   OR node.document_version_id IS DISTINCT FROM (head->>'document_version_id')::uuid
   OR node.digest IS DISTINCT FROM head->>'digest' OR node.kind IS DISTINCT FROM head->>'kind'
   OR node.policy_class IS DISTINCT FROM head->>'policy_class'
   OR node.known_at IS DISTINCT FROM (head->>'known_at')::timestamptz
   OR node.revision IS DISTINCT FROM (head->>'revision')::bigint
   OR node.status IS DISTINCT FROM head->>'status'
   OR node.valid_from IS DISTINCT FROM (head->>'valid_from')::timestamptz
   OR node.valid_until IS DISTINCT FROM (head->>'valid_until')::timestamptz THEN
   RETURN jsonb_build_object('qualified',false,'reason','source_or_ancestor_head_changed');
  END IF;
 END LOOP;
 RETURN jsonb_build_object('qualified',true,'authority_digest',capsule.authority_digest,'package_digest',ctx.package_digest);
EXCEPTION WHEN OTHERS THEN
 -- 失败关闭，不把任意SQL细节/私有来源正文返回研究进程。
 RETURN jsonb_build_object('qualified',false,'reason','authority_check_failed');
END $$;
REVOKE ALL ON FUNCTION design.qualify_research_chapter(uuid,text,design.sha256,bigint,bigint,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION design.qualify_research_chapter(uuid,text,design.sha256,bigint,bigint,uuid,uuid) TO tender_research_worker;
COMMIT;
