// Generated from packages/contracts/implemented.openapi.json. Run python scripts/generate-contracts.py.

export type DocumentEvidenceView = {
  document_version_id: string;
  parse_run_id: string | null;
  coverage: "pending" | "complete" | "partial" | "failed";
  gaps: string[];
  items: EvidenceView[];
};

export type DownloadGrant = {
  artifact_id: string;
  download_url: string;
  expires_at: string;
  filename: string;
  artifact_kind: string;
  presentation_revision: number | null;
};

export type EvidenceView = {
  id: string;
  document_version_id: string;
  parse_run_id: string;
  physical_page: number;
  printed_label: string | null;
  kind: "text" | "table" | "visual_observation";
  quote: string;
  bbox: number[] | null;
  page_width: number;
  page_height: number;
  rotation: 0 | 90 | 180 | 270;
  source_status: "published" | "withdrawn" | "candidate";
};

export type AuthView = {
  member: MemberView;
  csrf_token: string;
  expires_at: string;
};

export type Bid = {
  id: string;
  title: string;
  revision: number;
  material_revision: number;
  presentation_revision: number;
  phase: "preparation" | "working" | "submitted" | "closed" | "terminated";
  archived_at: string | null;
  current_body_id: string | null;
  current_review_id: string | null;
  stream_id: string;
  next_action: NextAction;
};

export type BidPage = {
  items: Bid[];
  next_cursor: string | null;
  has_more: boolean;
};

export type CreateBid = {
  title: string;
  engineering_project_id?: string;
  round_id?: string;
  bidder_id?: string;
  lot?: string;
  region?: string;
  engineering_type?: string;
  deadline?: string | null;
};

export type UploadGrant = {
  upload_id: string;
  object_ref: string;
  file_id: string;
  mode: "single" | "multipart";
  upload_url: string | null;
  part_size: number;
  part_count: number;
  parts: UploadPartGrant[];
  expires_at: string;
  session_expires_at: string;
  revision: number;
};

export type UploadRequest = {
  batch_id: string;
  role:
    | "tender"
    | "supplement"
    | "drawing"
    | "boq"
    | "body"
    | "official_result"
    | "other";
  filename: string;
  byte_size: number;
  declared_sha256: string;
  target: UploadTarget;
  declared_source_document_version_id?: string | null;
};

export type UploadView = {
  id: string;
  bid_id: string;
  batch_id: string;
  filename: string;
  state:
    | "authorized"
    | "uploaded"
    | "verifying"
    | "verified"
    | "quarantined"
    | "expired"
    | "aborted";
  document_version_id: string | null;
  error_code: string | null;
  file_id: string;
  revision: number;
  session_expires_at: string;
  binding_id: string;
  verified_retain_until: string | null;
};

export type UploadBatchView = {
  id: string;
  bid_id: string;
  revision: number;
  state: "open" | "verifying" | "committed" | "failed";
  uploads: UploadView[];
};

export type UploadBindingView = {
  id: string;
  upload_id: string;
  bid_id: string;
  file_id: string;
  expected_file_revision: number;
  previous_binding_id: string | null;
  upload_revision: number;
  verified_retain_until: string;
};

export type UploadBindingRequest = {
  target: UploadTarget;
  reason: string;
};

export type BatchCommit = {
  accepted_upload_ids: string[];
  explicit_partial_acceptance: boolean;
  accepted_binding_ids: string[];
};

export type MaterialView = {
  bid_id: string;
  material_revision: number;
  snapshot_id: string | null;
  documents: DocumentView[];
  conflicts: string[];
};

export type DocumentView = {
  id: string;
  bid_id: string;
  filename: string;
  role:
    | "tender"
    | "supplement"
    | "drawing"
    | "boq"
    | "body"
    | "official_result"
    | "other";
  artifact_kind:
    | "bid_body"
    | "review_report"
    | "annotated_review_copy"
    | "source_original"
    | "research_artifact";
  sha256: string;
  byte_size: number;
  media_type: string;
  obtained_at: string;
  parse_coverage: "pending" | "complete" | "partial" | "failed";
  file_id: string;
  file_revision: number;
  previous_version_id: string | null;
  file_head_revision: number;
  file_head_version_id: string;
  source_relation: DocumentRelation | null;
};

export type Accepted = {
  resource_id: string;
  operation: Operation;
  disposition: "created" | "reused" | "pending_latest";
  resource_kind:
    | "review"
    | "build"
    | "proposal"
    | "upload"
    | "import"
    | "task"
    | "message"
    | "report"
    | "pending_intent"
    | "action"
    | "comparison"
    | "experiment"
    | "index"
    | "ingestion";
  operation_url: string;
};

export type Operation = {
  id: string;
  bid_id: string;
  kind: string;
  state:
    | "queued"
    | "running"
    | "cancel_requested"
    | "succeeded"
    | "failed"
    | "cancelled";
  input_digest: string;
  handler_version: string;
  result_ref: string | null;
};

export type UploadComplete = {
  parts: UploadPartReceipt[];
  declared_sha256: string;
};

export type UploadTarget =
  | {
      kind: "new_file";
    }
  | {
      kind: "new_version";
      file_id: string;
      expected_file_revision: number;
    };

export type MemberView = {
  id: string;
  display_name: string;
  active: boolean;
};

export type NextAction = {
  action: string;
  reason: string;
  target_url: string;
  blocked: boolean;
};

export type UploadPartGrant = {
  part_number: number;
  byte_offset: number;
  byte_size: number;
  url: string;
  expires_at: string;
};

export type DocumentRelation = {
  source_document_version_id: string;
  kind: "user_declared_same_draft" | "controlled_render";
  verification: "unverified" | "verified" | "rejected";
  evidence_ids: string[];
};

export type UploadPartReceipt = {
  part_number: number;
  etag: string;
  byte_size: number;
};
