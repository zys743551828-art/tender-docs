-- 独立设计约束模型；不是 tender-app 的生产迁移。仅在全新隔离库执行。
BEGIN;
CREATE SCHEMA design;
SET search_path TO design, public;
CREATE DOMAIN sha256 AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
CREATE TABLE members (
  id uuid PRIMARY KEY, display_name text NOT NULL, active boolean NOT NULL DEFAULT true
);
CREATE TABLE bids (
  id uuid PRIMARY KEY, title text NOT NULL,
  revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
  material_revision bigint NOT NULL DEFAULT 1 CHECK (material_revision > 0),
  settings_revision bigint NOT NULL DEFAULT 1 CHECK (settings_revision > 0),
  presentation_revision bigint NOT NULL DEFAULT 1 CHECK (presentation_revision > 0),
  settings jsonb NOT NULL CHECK (jsonb_typeof(settings) = 'object'),
  created_by uuid NOT NULL REFERENCES members(id), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE blobs (
  id uuid PRIMARY KEY, digest sha256 NOT NULL UNIQUE, object_key text NOT NULL UNIQUE,
  byte_size bigint NOT NULL CHECK (byte_size >= 0), media_type text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE document_versions (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL REFERENCES bids(id), blob_id uuid NOT NULL REFERENCES blobs(id),
  original_name text NOT NULL, obtained_at timestamptz NOT NULL,
  artifact_kind text NOT NULL CHECK (artifact_kind IN ('bid_body','review_report','annotated_review_copy','source_original','research_artifact')),
  created_by uuid NOT NULL REFERENCES members(id), created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bid_id,id)
);
CREATE TABLE material_snapshots (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL REFERENCES bids(id), revision bigint NOT NULL CHECK (revision > 0),
  manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest) = 'object'), digest sha256 NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(bid_id,revision), UNIQUE(bid_id,id)
);
CREATE TABLE material_assignments (
  snapshot_id uuid NOT NULL, bid_id uuid NOT NULL, document_version_id uuid NOT NULL,
  role text NOT NULL CHECK(role IN ('tender','supplement','drawing','boq','body','official_result','other')),
  ordinal integer NOT NULL CHECK(ordinal >= 0),
  PRIMARY KEY(snapshot_id,role,ordinal),
  FOREIGN KEY(bid_id,snapshot_id) REFERENCES material_snapshots(bid_id,id),
  FOREIGN KEY(bid_id,document_version_id) REFERENCES document_versions(bid_id,id)
);
CREATE TABLE drafts (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL REFERENCES bids(id), revision bigint NOT NULL CHECK(revision > 0),
  UNIQUE(bid_id,id)
);
CREATE TABLE sections (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL, draft_id uuid NOT NULL,
  head_revision bigint NOT NULL CHECK(head_revision > 0), UNIQUE(bid_id,id), UNIQUE(bid_id,draft_id,id),
  FOREIGN KEY(bid_id,draft_id) REFERENCES drafts(bid_id,id)
);
CREATE TABLE section_revisions (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL, draft_id uuid NOT NULL, section_id uuid NOT NULL, revision bigint NOT NULL CHECK(revision > 0),
  schema_digest sha256 NOT NULL, content jsonb NOT NULL CHECK(jsonb_typeof(content) = 'object'), digest sha256 NOT NULL,
  created_by uuid NOT NULL REFERENCES members(id), created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(bid_id,draft_id,section_id) REFERENCES sections(bid_id,draft_id,id),
  UNIQUE(section_id,revision), UNIQUE(bid_id,id), UNIQUE(bid_id,draft_id,id)
);
CREATE TABLE draft_snapshots (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL, draft_id uuid NOT NULL, revision bigint NOT NULL CHECK(revision > 0),
  manifest jsonb NOT NULL CHECK(jsonb_typeof(manifest) = 'object'), digest sha256 NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), FOREIGN KEY(bid_id,draft_id) REFERENCES drafts(bid_id,id),
  UNIQUE(draft_id,revision), UNIQUE(bid_id,id), UNIQUE(bid_id,draft_id,id)
);
CREATE TABLE draft_snapshot_sections (
  snapshot_id uuid NOT NULL, bid_id uuid NOT NULL, draft_id uuid NOT NULL, section_revision_id uuid NOT NULL, ordinal integer NOT NULL CHECK(ordinal >= 0),
  PRIMARY KEY(snapshot_id,ordinal), UNIQUE(snapshot_id,section_revision_id),
  FOREIGN KEY(bid_id,draft_id,snapshot_id) REFERENCES draft_snapshots(bid_id,draft_id,id),
  FOREIGN KEY(bid_id,draft_id,section_revision_id) REFERENCES section_revisions(bid_id,draft_id,id)
);
CREATE TABLE commands (
  id uuid PRIMARY KEY, actor_id uuid NOT NULL REFERENCES members(id), command_kind text NOT NULL,
  target_id uuid NOT NULL, idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
  request_digest sha256 NOT NULL, response jsonb, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(actor_id,command_kind,target_id,idempotency_key)
);
CREATE TABLE operations (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL REFERENCES bids(id), command_id uuid NOT NULL REFERENCES commands(id),
  kind text NOT NULL, input_digest sha256 NOT NULL,
  state text NOT NULL CHECK(state IN ('queued','running','cancel_requested','succeeded','failed','cancelled')),
  handler_version text NOT NULL, cancel_requested_at timestamptz, finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(bid_id,id),
  CHECK((state IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)),
  CHECK(state NOT IN ('cancel_requested','cancelled') OR cancel_requested_at IS NOT NULL)
);
CREATE UNIQUE INDEX active_same_input ON operations(bid_id,kind,input_digest)
  WHERE state IN ('queued','running','cancel_requested');
CREATE TABLE step_receipts (
  operation_id uuid NOT NULL REFERENCES operations(id), step_key text NOT NULL, input_digest sha256 NOT NULL,
  fence bigint NOT NULL CHECK(fence > 0), attempt_count integer NOT NULL CHECK(attempt_count > 0),
  state text NOT NULL CHECK(state IN ('running','succeeded','failed','cancelled')),
  heartbeat_at timestamptz, output jsonb,
  PRIMARY KEY(operation_id,step_key,input_digest)
);
CREATE TABLE effect_receipts (
  operation_id uuid NOT NULL REFERENCES operations(id), effect_key text NOT NULL,
  result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(operation_id,effect_key)
);
CREATE TABLE review_inputs (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL, material_snapshot_id uuid NOT NULL, body_version_id uuid NOT NULL,
  input_digest sha256 NOT NULL, method_digest sha256 NOT NULL, manifest jsonb NOT NULL CHECK(jsonb_typeof(manifest)='object'),
  frozen_at timestamptz NOT NULL,
  FOREIGN KEY(bid_id,material_snapshot_id) REFERENCES material_snapshots(bid_id,id),
  FOREIGN KEY(bid_id,body_version_id) REFERENCES document_versions(bid_id,id), UNIQUE(bid_id,id)
);
CREATE TABLE review_runs (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL, input_id uuid NOT NULL, operation_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(bid_id,id),
  FOREIGN KEY(bid_id,input_id) REFERENCES review_inputs(bid_id,id),
  FOREIGN KEY(bid_id,operation_id) REFERENCES operations(bid_id,id)
);
CREATE TABLE review_slots (
  bid_id uuid PRIMARY KEY REFERENCES bids(id), active_review_id uuid,
  pending_intent jsonb CHECK(pending_intent IS NULL OR jsonb_typeof(pending_intent) = 'object'),
  revision bigint NOT NULL CHECK(revision > 0),
  FOREIGN KEY(bid_id,active_review_id) REFERENCES review_runs(bid_id,id)
);
CREATE TABLE review_results (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL, review_id uuid NOT NULL,
  core_coverage text NOT NULL CHECK(core_coverage IN ('complete','partial','failed')),
  payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(bid_id,review_id) REFERENCES review_runs(bid_id,id), UNIQUE(bid_id,id)
);
CREATE TABLE build_inputs (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL, draft_snapshot_id uuid NOT NULL,
  template_digest sha256 NOT NULL, render_digest sha256 NOT NULL, manifest jsonb NOT NULL,
  FOREIGN KEY(bid_id,draft_snapshot_id) REFERENCES draft_snapshots(bid_id,id), UNIQUE(bid_id,id)
);
CREATE TABLE builds (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL, input_id uuid NOT NULL, operation_id uuid NOT NULL,
  FOREIGN KEY(bid_id,input_id) REFERENCES build_inputs(bid_id,id),
  FOREIGN KEY(bid_id,operation_id) REFERENCES operations(bid_id,id), UNIQUE(bid_id,id)
);
CREATE TABLE build_outputs (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL, build_id uuid NOT NULL, format text NOT NULL CHECK(format IN ('docx','pdf')),
  document_version_id uuid, state text NOT NULL CHECK(state IN ('succeeded','failed')),
  gaps jsonb NOT NULL CHECK(jsonb_typeof(gaps) = 'array'),
  FOREIGN KEY(bid_id,build_id) REFERENCES builds(bid_id,id),
  FOREIGN KEY(bid_id,document_version_id) REFERENCES document_versions(bid_id,id),
  UNIQUE(build_id,format), CHECK((state='succeeded') = (document_version_id IS NOT NULL))
);
CREATE TABLE submission_records (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL REFERENCES bids(id), body_version_id uuid NOT NULL, review_id uuid,
  kind text NOT NULL CHECK(kind IN ('standard','offline_fact')), gate_snapshot jsonb,
  evidence jsonb NOT NULL, submitted_at timestamptz NOT NULL, created_by uuid NOT NULL REFERENCES members(id),
  supersedes_id uuid, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(bid_id,id),
  FOREIGN KEY(bid_id,body_version_id) REFERENCES document_versions(bid_id,id),
  FOREIGN KEY(bid_id,review_id) REFERENCES review_runs(bid_id,id),
  FOREIGN KEY(bid_id,supersedes_id) REFERENCES submission_records(bid_id,id),
  CHECK(kind <> 'standard' OR (review_id IS NOT NULL AND gate_snapshot IS NOT NULL))
);
CREATE FUNCTION validate_standard_submission() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE reviewed_body uuid; selected_kind text;
BEGIN
  IF NEW.kind = 'standard' THEN
    SELECT i.body_version_id INTO reviewed_body
      FROM design.review_runs r JOIN design.review_inputs i ON i.bid_id=r.bid_id AND i.id=r.input_id
      WHERE r.bid_id=NEW.bid_id AND r.id=NEW.review_id;
    SELECT artifact_kind INTO selected_kind FROM design.document_versions WHERE bid_id=NEW.bid_id AND id=NEW.body_version_id;
    IF reviewed_body IS DISTINCT FROM NEW.body_version_id OR selected_kind IS DISTINCT FROM 'bid_body' THEN
      RAISE EXCEPTION 'submission_body_mismatch' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER submission_identity BEFORE INSERT ON submission_records
  FOR EACH ROW EXECUTE FUNCTION validate_standard_submission();
CREATE TABLE submission_heads (
  bid_id uuid PRIMARY KEY REFERENCES bids(id), submission_id uuid NOT NULL,
  revision bigint NOT NULL CHECK(revision > 0),
  FOREIGN KEY(bid_id,submission_id) REFERENCES submission_records(bid_id,id)
);
CREATE TABLE proposed_actions (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL REFERENCES bids(id), base_revision bigint NOT NULL CHECK(base_revision > 0),
  request_digest sha256 NOT NULL, expires_at timestamptz NOT NULL, payload jsonb NOT NULL
);
CREATE TABLE action_consumptions (
  action_id uuid PRIMARY KEY REFERENCES proposed_actions(id), command_id uuid NOT NULL REFERENCES commands(id),
  consumed_by uuid NOT NULL REFERENCES members(id), result jsonb NOT NULL, consumed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE outbox (
  id uuid PRIMARY KEY, operation_id uuid REFERENCES operations(id), topic text NOT NULL,
  deduplication_key text NOT NULL UNIQUE, payload jsonb NOT NULL,
  available_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz, publish_attempts integer NOT NULL DEFAULT 0 CHECK(publish_attempts >= 0)
);
CREATE INDEX outbox_due ON outbox(available_at,id) WHERE published_at IS NULL;
CREATE TABLE stream_heads (
  id uuid PRIMARY KEY, bid_id uuid NOT NULL REFERENCES bids(id), last_seq bigint NOT NULL DEFAULT 0 CHECK(last_seq >= 0)
);
CREATE TABLE stream_events (
  stream_id uuid NOT NULL REFERENCES stream_heads(id), seq bigint NOT NULL CHECK(seq > 0),
  event_id uuid NOT NULL UNIQUE, event_type text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(stream_id,seq)
);
CREATE TABLE audit_entries (
  id uuid PRIMARY KEY, actor_id uuid REFERENCES members(id), bid_id uuid REFERENCES bids(id),
  command_id uuid REFERENCES commands(id), event_type text NOT NULL, payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable_record' USING ERRCODE='55000'; END;
$$;
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['blobs','document_versions','material_snapshots','material_assignments',
    'section_revisions','draft_snapshots','draft_snapshot_sections','review_inputs','review_runs','review_results',
    'build_inputs','build_outputs','submission_records','effect_receipts','proposed_actions','action_consumptions','stream_events','audit_entries']
  LOOP
    EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',table_name);
  END LOOP;
END;
$$;
-- TRUNCATE与DDL由数据库角色权限禁止；生产运行账号不得拥有表与schema。
COMMIT;
