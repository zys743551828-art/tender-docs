CREATE FUNCTION qa.commit_upload(upload uuid,binding uuid) RETURNS text LANGUAGE plpgsql AS $$
DECLARE b uuid; chosen design.upload_bindings; f design.document_files; t design.upload_transfers; v design.uploads;
BEGIN
 SELECT bid_id INTO b FROM design.uploads WHERE id=upload;
 PERFORM 1 FROM design.bids WHERE id=b FOR UPDATE;
 SELECT * INTO v FROM design.uploads WHERE id=upload FOR UPDATE;
 SELECT * INTO t FROM design.upload_transfers WHERE upload_id=upload FOR UPDATE;
 IF t.committed_binding_id IS NOT NULL THEN
 IF t.committed_binding_id=binding THEN RETURN 'REUSE'; END IF;
 RAISE EXCEPTION 'UPLOAD_NOT_REBINDABLE' USING ERRCODE='55000'; END IF;
 IF t.current_binding_id<>binding THEN RAISE EXCEPTION 'BINDING_STALE' USING ERRCODE='55000'; END IF;
 SELECT * INTO chosen FROM design.upload_bindings WHERE id=binding;
 SELECT * INTO f FROM design.document_files WHERE id=chosen.file_id FOR UPDATE;
 IF v.verified_retain_until<=clock_timestamp() THEN RAISE EXCEPTION 'VERIFIED_BYTES_EXPIRED' USING ERRCODE='55000'; END IF;
 IF f.revision<>chosen.expected_file_revision THEN RAISE EXCEPTION 'FILE_REVISION_CONFLICT' USING ERRCODE='40001'; END IF;
 INSERT INTO design.document_file_versions VALUES(b,f.id,v.verified_version_id,f.revision,f.head_version_id);
 UPDATE design.document_files SET revision=revision+1,head_version_id=v.verified_version_id WHERE id=f.id;
 UPDATE design.upload_transfers SET committed_binding_id=binding WHERE upload_id=upload;
 UPDATE design.upload_batches SET state='committed',revision=revision+1 WHERE id=v.batch_id;
 RETURN 'COMMITTED'; END $$;
CREATE FUNCTION qa.rebind(upload uuid,target_file uuid,expected bigint,new_id uuid,command uuid,key text) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE b uuid; t design.upload_transfers; v design.uploads; f design.document_files; replay jsonb;
BEGIN
 SELECT response INTO replay FROM design.commands WHERE actor_id='00000000-0000-0000-0000-000000000001' AND command_kind='rebind' AND target_id=upload AND idempotency_key=key;
 IF FOUND THEN RETURN (replay->>'binding_id')::uuid; END IF;
 SELECT bid_id INTO b FROM design.uploads WHERE id=upload; PERFORM 1 FROM design.bids WHERE id=b FOR UPDATE;
 SELECT * INTO v FROM design.uploads WHERE id=upload FOR UPDATE;
 SELECT * INTO t FROM design.upload_transfers WHERE upload_id=upload FOR UPDATE;
 SELECT * INTO f FROM design.document_files WHERE id=target_file FOR UPDATE;
 IF f.bid_id<>b OR f.role<>v.role THEN RAISE EXCEPTION 'UPLOAD_TARGET_MISMATCH' USING ERRCODE='23514'; END IF;
 IF v.state<>'verified' OR t.committed_binding_id IS NOT NULL THEN RAISE EXCEPTION 'UPLOAD_NOT_REBINDABLE' USING ERRCODE='55000'; END IF;
 IF v.verified_retain_until<=clock_timestamp() THEN RAISE EXCEPTION 'VERIFIED_BYTES_EXPIRED' USING ERRCODE='55000'; END IF;
 IF f.revision<>expected THEN RAISE EXCEPTION 'FILE_REVISION_CONFLICT' USING ERRCODE='40001'; END IF;
 INSERT INTO design.commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest,response) VALUES(command,'00000000-0000-0000-0000-000000000001','rebind',upload,key,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',jsonb_build_object('binding_id',new_id));
 INSERT INTO design.upload_bindings(id,bid_id,upload_id,file_id,expected_file_revision,previous_binding_id,reason,command_id,created_by) VALUES(new_id,b,upload,target_file,expected,t.current_binding_id,'测试显式选择',command,'00000000-0000-0000-0000-000000000001');
 UPDATE design.upload_transfers SET current_binding_id=new_id,revision=revision+1 WHERE upload_id=upload;
 INSERT INTO design.audit_entries(id,actor_id,bid_id,command_id,event_type,payload) VALUES(new_id,'00000000-0000-0000-0000-000000000001',b,command,'upload.target_reconfirmed',jsonb_build_object('previous',t.current_binding_id,'next',new_id));
 RETURN new_id; END $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA qa TO qa_api;