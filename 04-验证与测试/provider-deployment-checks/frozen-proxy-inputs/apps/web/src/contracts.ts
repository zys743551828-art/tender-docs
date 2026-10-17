// Generated from packages/contracts/implemented.openapi.json. Run python scripts/generate-contracts.py.

export type RetrievalRequest = {
  query: string;
  context: "assistant" | "authoring" | "body_evidence" | "history";
  bid_id: string | null;
  limit: number;
};

export type RetrievalResult = {
  trace_id: string;
  policy_digest: string;
  hits: RetrievalHit[];
  coverage: "available" | "partial" | "unavailable";
  gaps: string[];
};

export type RetrievalHit = {
  chunk_id: string;
  source_version_id: string;
  parse_run_id: string;
  physical_page: number;
  citation_id: string;
  excerpt: string;
  embedding_profile_id: string;
  rank: number;
};

export type ReviewManifest = {
  bid_id: string;
  body_version_id: string;
  material_snapshot_id: string;
  material_revision: number;
  rule_set_id: string;
  fact_set_id: string | null;
  parse_run_ids: string[];
  method_digest: string;
  policy_digest: string;
  purpose: "compliance_and_scoring";
  source_refs: SourceRef[];
  content_checks: {
    consistency: boolean;
    scope_residue: boolean;
  };
  frozen_at: string;
  as_of: string;
  evaluation_context_digest: string;
  evidence_manifest_digest: string;
};

export type ReviewView = {
  id: string;
  bid_id: string;
  operation_id: string;
  input_digest: string;
  core_coverage: "pending" | "complete" | "partial" | "failed";
  risk_count: number | null;
  current: boolean;
  prediction: PredictionView;
  next_action: NextAction;
  source_impact_revision: number;
  manifest_digest: string;
  coverage_layers: ReviewCoverage;
};

export type RuleSetView = {
  id: string;
  bid_id: string;
  material_revision: number;
  coverage: "complete" | "partial" | "conflict";
  rules: RuleView[];
  revision: number;
};

export type FactSetView = {
  id: string | null;
  bid_id: string;
  facts: FactView[];
  revision: number;
};

export type FindingPage = {
  items: FindingView[];
  next_cursor: string | null;
  has_more: boolean;
};

export type MatrixView = {
  review_id: string;
  coverage: "pending" | "complete" | "partial" | "failed";
  rows: MatrixRow[];
  coverage_layers: ReviewCoverage;
};

export type SettingsView = {
  bid_id: string;
  revision: number;
  settings: ProjectSettings;
};

export type DecisionRequest = {
  decision: string;
  reason: string;
  evidence_ids: string[];
};

export type BatchRuleConfirmationRequest = {
  items: RuleConfirmationItem[];
};

export type BatchRuleConfirmationView = {
  rule_set_id: string;
  revision: number;
  items: RuleConfirmationResult[];
};

export type FactChangeRequest = {
  fact: FactInput;
  replaces_fact_id: string | null;
};

export type Cancellation = {
  reason: string;
  cancel_pending_same_input: boolean;
};

export type DiagnosticPage = {
  items: ValidationDiagnostic[];
  next_cursor: string | null;
};

export type ReviewRequest = {
  body_version_id: string;
  material_revision: number;
  trigger: "manual" | "automatic" | "build_follow_up";
  rerun_nonce?: string | null;
};

export type RuleSetCorrectionRequest = {
  base_rule_set_id: string;
  reason: string;
  additions: RuleAddition[];
};

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

export type SourceRef = {
  id: string;
  digest: string;
  kind: "tender" | "supplement" | "body" | "prebid_fact";
  known_at: string;
  lineage_manifest_digest: string;
};

export type PredictionView = {
  review_id: string;
  mode: "hidden" | "partial" | "raw_uncalibrated" | "accepted" | "unavailable";
  maximum: string | null;
  reasons: string[];
  method_digest: string;
  value?: string;
  interval?: {
    lower: string;
    upper: string;
  };
};

export type NextAction = {
  action: string;
  reason: string;
  target_url: string;
  blocked: boolean;
};

export type ReviewCoverage = {
  source_scope: CoverageLayer;
  core_extraction: CoverageLayer;
  judgment: CoverageLayer;
  scoring: CoverageLayer;
  extraction_quality: "not_measured" | "measured";
  quality_report_id: string | null;
  human_confirmation_pending: number;
};

export type RuleView = {
  id: string;
  rule_set_id: string;
  kind: "disqualification" | "mandatory" | "scoring" | "advisory";
  statement: string;
  conditions: string[];
  exceptions: string[];
  maximum: string | null;
  evidence_ids: string[];
  confirmation: "pending" | "confirmed" | "rejected" | "deferred";
  decision_revision: number;
  carryover: RuleCarryoverView | null;
  scope: RuleScope;
  bands: RuleBand[];
  origin_kind: "model_extracted" | "human_added" | "carried_human";
  manual_origin_rule_id: string | null;
  provenance_status: "effective" | "needs_revalidation" | "superseded";
};

export type FactView = {
  id: string;
  subject: string;
  scope: string;
  name: string;
  value: string | boolean | null;
  unit: string | null;
  evidence_ids: string[];
  confirmation: "pending" | "confirmed" | "rejected";
  decision_revision: number;
};

export type FindingView = {
  id: string;
  review_id: string;
  rule_id: string | null;
  category:
    | "disqualification"
    | "response"
    | "consistency"
    | "scope"
    | "improvement";
  severity: "critical" | "major" | "minor" | "info";
  verdict:
    | "satisfied"
    | "violated"
    | "not_found"
    | "unable_to_judge"
    | "not_applicable";
  statement: string;
  evidence_ids: string[];
  searched_ranges: string[];
  decision: DecisionView | null;
  decision_revision: number;
  origin_review_id: string;
  retained_known_critical: boolean;
  detection_run_state: "ran" | "not_run_this_round";
  lifecycle: "open" | "deferred" | "resolved_by_recheck" | "dismissed";
};

export type MatrixRow = {
  rule: RuleView;
  verdict:
    | "satisfied"
    | "violated"
    | "not_found"
    | "unable_to_judge"
    | "not_applicable"
    | null;
  finding_ids: string[];
  evidence_ids: string[];
  check_status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  error_code: string | null;
};

export type ProjectSettings = {
  auto_review: boolean;
  consistency: boolean;
  scope_residue: boolean;
  show_prediction: boolean;
  pricing: boolean;
  result_reminder: boolean;
  accept_rules_as_written: boolean;
};

export type RuleConfirmationItem = {
  rule_id: string;
  expected_decision_revision: number;
};

export type RuleConfirmationResult = {
  rule_id: string;
  status: "confirmed" | "skipped";
  decision_revision: number;
  decision_id: string | null;
};

export type FactInput = {
  subject: string;
  scope: string;
  name: string;
  value: string | boolean | null;
  unit: string | null;
  evidence_ids: string[];
  reason: string;
};

export type ValidationDiagnostic = {
  id: string;
  operation_id: string;
  step_key: string;
  code:
    | "quote_not_found"
    | "source_not_allowed"
    | "unsupported_quantity"
    | "unit_mismatch"
    | "scope_mismatch"
    | "invalid_structure"
    | "incomplete_range";
  field_path: string;
  evidence_ids: string[];
  action: "rejected" | "needs_review";
  message: string;
};

export type RuleAddition = {
  kind: "disqualification" | "mandatory" | "scoring" | "advisory";
  statement: string;
  conditions: string[];
  exceptions: string[];
  scope: RuleScope;
  maximum: string | null;
  bands: RuleBand[];
  evidence_quotes: RuleEvidenceQuote[];
};

export type MemberView = {
  id: string;
  display_name: string;
  active: boolean;
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

export type CoverageLayer = {
  state: "pending" | "complete" | "partial" | "failed" | "not_run";
  planned: number;
  completed: number;
  denominator_basis: string;
  gaps: string[];
};

export type RuleCarryoverView = {
  from_rule_id: string;
  from_decision_id: string;
  equivalence_digest: string;
  adopted_at: string;
};

export type RuleScope = {
  bid_id: string;
  description: string;
};

export type RuleBand = {
  label: string;
  description: string;
  points: string;
  evidence_ids: string[];
};

export type DecisionView = {
  id: string;
  object_id: string;
  object_revision: number;
  decision: string;
  reason: string;
  actor_id: string;
  created_at: string;
};

export type RuleEvidenceQuote = {
  evidence_id: string;
  quote: string;
};

export const implementedOperations: readonly string[] = [
  "GET /health/live",
  "GET /health/ready",
  "POST /api/v1/auth/login",
  "GET /api/v1/auth/session",
  "POST /api/v1/auth/logout",
  "POST /api/v1/bids",
  "GET /api/v1/bids",
  "GET /api/v1/bids/{bid_id}",
  "POST /api/v1/bids/{bid_id}/upload-batches",
  "GET /api/v1/bids/{bid_id}/upload-batches/{batch_id}",
  "POST /api/v1/bids/{bid_id}/uploads",
  "GET /api/v1/uploads/{upload_id}",
  "POST /api/v1/uploads/{upload_id}/complete",
  "POST /api/v1/uploads/{upload_id}/bindings",
  "POST /api/v1/bids/{bid_id}/upload-batches/{batch_id}/commit",
  "GET /api/v1/bids/{bid_id}/materials",
  "GET /api/v1/operations/{operation_id}",
  "POST /api/v1/operations/{operation_id}/cancel",
  "GET /api/v1/evidence/{evidence_id}",
  "GET /api/v1/documents/{document_version_id}",
  "GET /api/v1/documents/{document_version_id}/evidence",
  "GET /api/v1/artifacts/{artifact_id}/download",
  "POST /api/v1/bids/{bid_id}/reviews",
  "GET /api/v1/uploads/{upload_id}/parts",
  "POST /api/v1/uploads/{upload_id}/authorizations",
  "POST /api/v1/uploads/{upload_id}/abort",
];
