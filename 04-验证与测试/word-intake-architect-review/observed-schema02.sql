-- Additive capability for a fresh isolated database only; no running database patch.
BEGIN;
SET search_path TO design,public;

-- This is an immutable inspection receipt, never a second document/blob inventory.
CREATE TABLE word_intake_receipts (
 document_version_id uuid PRIMARY KEY,
 bid_id uuid NOT NULL,
 upload_id uuid NOT NULL UNIQUE,
 verify_operation_id uuid NOT NULL UNIQUE,
 inspection_digest sha256 NOT NULL,
 inspection_report jsonb NOT NULL CHECK(jsonb_typeof(inspection_report)='object'),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(bid_id,document_version_id),
 FOREIGN KEY(bid_id,document_version_id) REFERENCES document_versions(bid_id,id),
 FOREIGN KEY(bid_id,upload_id) REFERENCES uploads(bid_id,id),
 FOREIGN KEY(bid_id,verify_operation_id) REFERENCES operations(bid_id,id)
);
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON word_intake_receipts FOR EACH ROW EXECUTE FUNCTION design.append_only();

-- The explicit PDF source selection must survive upload completion and process restart.
CREATE TABLE word_pdf_declarations (
 upload_id uuid PRIMARY KEY,
 bid_id uuid NOT NULL,
 source_document_version_id uuid NOT NULL,
 command_id uuid NOT NULL REFERENCES commands(id),
 created_by uuid NOT NULL REFERENCES members(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(bid_id,upload_id) REFERENCES uploads(bid_id,id),
 FOREIGN KEY(bid_id,source_document_version_id) REFERENCES word_intake_receipts(bid_id,document_version_id)
);
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON word_pdf_declarations FOR EACH ROW EXECUTE FUNCTION design.append_only();

CREATE TABLE word_render_runs (
 operation_id uuid PRIMARY KEY,
 bid_id uuid NOT NULL,
 source_document_version_id uuid NOT NULL,
 profile_digest sha256 NOT NULL,
 frozen_profile jsonb NOT NULL CHECK(jsonb_typeof(frozen_profile)='object'),
 pdf_document_version_id uuid,
 outcome jsonb CHECK(outcome IS NULL OR coalesce((jsonb_typeof(outcome)='object' AND outcome ?& ARRAY['state','coverage','gaps','chain','conversion_attempted'] AND jsonb_typeof(outcome->'state')='string' AND outcome->>'state' IN ('succeeded','failed') AND jsonb_typeof(outcome->'coverage')='string' AND outcome->>'coverage' IN ('complete','partial','failed') AND jsonb_typeof(outcome->'gaps')='array' AND jsonb_typeof(outcome->'chain')='object' AND jsonb_typeof(outcome->'conversion_attempted')='boolean'),false)),
 finished_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(bid_id,operation_id) REFERENCES operations(bid_id,id),
 FOREIGN KEY(bid_id,source_document_version_id) REFERENCES word_intake_receipts(bid_id,document_version_id),
 FOREIGN KEY(bid_id,pdf_document_version_id) REFERENCES document_versions(bid_id,id),
 CHECK((outcome IS NULL)=(finished_at IS NULL)),
 CHECK(outcome IS NULL OR coalesce(((outcome->>'state'='succeeded')=(pdf_document_version_id IS NOT NULL)),false)),
 CHECK(outcome IS NULL OR coalesce((outcome->>'state'<>'succeeded' OR outcome->'conversion_attempted'='true'::jsonb),false)),
 CHECK(pdf_document_version_id IS DISTINCT FROM source_document_version_id),
 CHECK(outcome IS NULL OR coalesce((outcome->>'coverage'<>'complete' OR (outcome->>'state'='succeeded' AND outcome->'gaps'='[]'::jsonb)),false))
);
CREATE FUNCTION design.word_intake_integrity() RETURNS trigger LANGUAGE plpgsql SET search_path=design,pg_temp AS $$
DECLARE original uploads; source_receipt word_intake_receipts; source_digest text; output_digest text; output_media text; identity_key text;
BEGIN
 IF TG_TABLE_NAME='word_intake_receipts' THEN
  SELECT * INTO STRICT original FROM uploads WHERE id=NEW.upload_id AND bid_id=NEW.bid_id;
  IF original.state<>'verified' OR original.verified_version_id IS DISTINCT FROM NEW.document_version_id
   OR NOT EXISTS(SELECT 1 FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=NEW.document_version_id AND v.bid_id=NEW.bid_id AND b.digest=original.declared_digest AND b.media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
   OR NEW.inspection_report->>'schema' IS DISTINCT FROM 'word-intake-inspection/1'
  THEN RAISE EXCEPTION 'word_original_identity_invalid' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='word_pdf_declarations' THEN
  SELECT * INTO STRICT original FROM uploads WHERE id=NEW.upload_id AND bid_id=NEW.bid_id;
  SELECT * INTO STRICT source_receipt FROM word_intake_receipts WHERE document_version_id=NEW.source_document_version_id AND bid_id=NEW.bid_id;
  IF original.filename !~* '\.pdf$' OR original.role IS DISTINCT FROM (SELECT role FROM uploads WHERE id=source_receipt.upload_id)
  THEN RAISE EXCEPTION 'word_pdf_declaration_scope_invalid' USING ERRCODE='23514'; END IF;
 ELSE
  IF TG_OP='UPDATE' AND (OLD.outcome IS NOT NULL OR ROW(NEW.operation_id,NEW.bid_id,NEW.source_document_version_id,NEW.profile_digest,NEW.frozen_profile,NEW.created_at) IS DISTINCT FROM ROW(OLD.operation_id,OLD.bid_id,OLD.source_document_version_id,OLD.profile_digest,OLD.frozen_profile,OLD.created_at))
  THEN RAISE EXCEPTION 'word_render_receipt_immutable' USING ERRCODE='55000'; END IF;
  IF NEW.outcome IS NOT NULL AND NEW.outcome->>'state'='succeeded' THEN
   SELECT b.digest INTO STRICT source_digest FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=NEW.source_document_version_id AND v.bid_id=NEW.bid_id;
   SELECT b.digest,b.media_type INTO STRICT output_digest,output_media FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=NEW.pdf_document_version_id AND v.bid_id=NEW.bid_id;
   IF output_media<>'application/pdf' OR NEW.outcome->'chain'->>'source_docx_digest' IS DISTINCT FROM source_digest OR NEW.outcome->'chain'->>'pdf_digest' IS DISTINCT FROM output_digest OR NEW.outcome->'chain'->>'profile_digest' IS DISTINCT FROM NEW.profile_digest
   THEN RAISE EXCEPTION 'word_render_chain_invalid' USING ERRCODE='23514'; END IF;
   FOREACH identity_key IN ARRAY ARRAY['renderer_digest','fontset_digest','options_digest'] LOOP
    IF NEW.frozen_profile->'identity'->>identity_key IS NULL OR NEW.outcome->'chain'->>identity_key IS DISTINCT FROM NEW.frozen_profile->'identity'->>identity_key
    THEN RAISE EXCEPTION 'word_render_method_invalid' USING ERRCODE='23514'; END IF;
   END LOOP;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER word_receipt_scope BEFORE INSERT ON word_intake_receipts FOR EACH ROW EXECUTE FUNCTION design.word_intake_integrity();
CREATE TRIGGER word_declaration_scope BEFORE INSERT ON word_pdf_declarations FOR EACH ROW EXECUTE FUNCTION design.word_intake_integrity();
CREATE TRIGGER word_render_scope BEFORE INSERT OR UPDATE ON word_render_runs FOR EACH ROW EXECUTE FUNCTION design.word_intake_integrity();
CREATE TRIGGER immutable_guard BEFORE DELETE ON word_render_runs FOR EACH ROW EXECUTE FUNCTION design.append_only();
REVOKE ALL ON FUNCTION design.word_intake_integrity() FROM PUBLIC;

REVOKE ALL ON word_intake_receipts,word_pdf_declarations FROM PUBLIC;
REVOKE ALL ON word_render_runs FROM PUBLIC;
GRANT SELECT ON word_intake_receipts,word_pdf_declarations TO tender_api,tender_worker;
GRANT INSERT ON word_intake_receipts TO tender_worker;
GRANT INSERT ON word_pdf_declarations TO tender_api;
GRANT SELECT ON word_render_runs TO tender_api,tender_worker;
GRANT INSERT(operation_id,bid_id,source_document_version_id,profile_digest,frozen_profile) ON word_render_runs TO tender_api;
GRANT INSERT ON word_render_runs TO tender_worker;
GRANT UPDATE(pdf_document_version_id,outcome,finished_at) ON word_render_runs TO tender_worker;
COMMIT;
