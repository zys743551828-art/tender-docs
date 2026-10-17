-- 能力角色不含密码；部署账号由密钥服务另建，业务进程不得使用初始化owner。
BEGIN;
DO $$ DECLARE n text; BEGIN
 FOREACH n IN ARRAY ARRAY['tender_api','tender_worker','tender_score_read','tender_research_read','tender_research_worker'] LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=n) THEN EXECUTE format('CREATE ROLE %I NOLOGIN',n); END IF;
 END LOOP;
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA design,retrieval FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA design,retrieval FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA design,retrieval FROM PUBLIC;
GRANT USAGE ON SCHEMA design,retrieval TO tender_api,tender_worker,tender_score_read,tender_research_read;
GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA design,retrieval TO tender_api;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA design,retrieval TO tender_api;
GRANT SELECT ON design.bids,design.bid_profiles,design.blobs,design.document_versions,design.material_snapshots,design.material_assignments,design.parse_runs,design.pages,design.evidence_blocks,design.rule_sets,design.rule_items,design.fact_sets,design.fact_items,design.decisions,design.review_inputs,design.review_runs,design.findings,design.chapter_packages,design.drafts,design.sections,design.section_revisions,design.draft_snapshots,design.draft_snapshot_sections,design.template_versions TO tender_worker;
GRANT SELECT,INSERT,UPDATE ON design.operations,design.operation_steps,design.step_receipts,design.effect_receipts,design.outbox,design.model_calls,design.review_results,design.predictions,design.builds,design.build_inputs,design.build_outputs,design.item_judgments TO tender_worker;
GRANT EXECUTE ON FUNCTION design.claim_outbox(integer,uuid),design.ack_outbox(uuid,uuid),design.renew_outbox(uuid,uuid),design.renew_step(uuid,text,design.sha256,bigint,uuid) TO tender_worker;
GRANT SELECT,INSERT,UPDATE ON design.uploads,design.upload_batches,design.blobs,design.document_versions,design.material_snapshots,design.material_assignments,design.parse_runs,design.pages,design.evidence_blocks,design.rule_sets,design.rule_items,design.fact_sets,design.fact_items,design.findings,design.chapter_packages,design.audit_entries,design.stream_heads,design.stream_events,design.review_slots,design.artifact_presentations TO tender_worker;
GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA retrieval TO tender_worker;
GRANT EXECUTE ON FUNCTION retrieval.eligible_chunks(uuid[],timestamptz,uuid[],boolean) TO tender_worker;
GRANT UPDATE(current_review_id,current_body_id) ON design.bid_profiles TO tender_worker;
CREATE FUNCTION retrieval.formal_score_context(target_review uuid)
RETURNS SETOF retrieval.chunks LANGUAGE sql STABLE SECURITY DEFINER SET search_path=retrieval,design,pg_temp AS $$
 SELECT c.id,c.source_id,c.parse_id,c.physical_page,c.ordinal,c.chapter_path,c.evidence_ids,c.text_content,c.lexemes,c.lex_tsv,c.token_count,c.digest
 FROM retrieval.score_context_chunks c WHERE c.review_id=target_review;
$$;
REVOKE ALL ON FUNCTION retrieval.formal_score_context(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION retrieval.formal_score_context(uuid) TO tender_score_read;
GRANT SELECT ON design.samples,design.experiments,design.model_artifacts,design.model_acceptances TO tender_research_read;
GRANT SELECT ON design.document_files,design.document_file_versions,design.upload_transfers,design.upload_parts,design.fact_heads,design.fact_set_changes,design.rule_carryovers,design.decision_heads TO tender_worker;
GRANT INSERT,UPDATE ON design.document_files,design.upload_transfers,design.upload_parts TO tender_worker;
GRANT INSERT ON design.document_file_versions,design.rule_carryovers TO tender_worker;
GRANT SELECT,INSERT,UPDATE ON design.matrix_checks,design.validation_diagnostics,design.shadow_comparisons TO tender_worker;
GRANT USAGE ON SCHEMA design TO tender_research_worker;
GRANT SELECT ON design.experiments,design.experiment_previews,design.samples,design.model_artifacts,design.blobs,design.bids TO tender_research_worker;
GRANT SELECT,INSERT,UPDATE ON design.research_runs,design.research_run_items,design.research_run_attempts,design.commands,design.operations,design.operation_steps,design.outbox,design.audit_entries TO tender_research_worker;
GRANT EXECUTE ON FUNCTION design.reserve_research_fee(uuid,design.nonnegative_amount) TO tender_research_worker;
-- 研究标签由专用受控导出服务核验后生成manifest；研究角色不直接扫描全量outcomes。
COMMIT;
