"""Generate only schema types consumed by this UI, without inventing API routes."""

import argparse
import json
from pathlib import Path

root = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument(
    "--openapi", type=Path, default=root / "packages/contracts/implemented.openapi.json"
)
parser.add_argument("--output", type=Path, default=root / "apps/web/src/contracts.ts")
parser.add_argument(
    "--schemas", help="Generate only these comma-separated schema roots, without capability flags"
)
args = parser.parse_args()
contract = json.loads(args.openapi.read_text())
schemas = contract["components"]["schemas"]
wanted = [
    "UploadLayoutView",
    "KnowledgeCandidate",
    "KnowledgeView",
    "KnowledgePage",
    "KnowledgeVersionRequest",
    "KnowledgeVersionPage",
    "KnowledgeReferenceRequest",
    "KnowledgeReferenceHistory",
    "KnowledgeImpact",
    "KnowledgeSourcePositions",
    "BiddingCompanyPage",
    "DefaultBiddingCompanyView",
    "EngineeringIdentityPage",
    "EngineeringRoundPage",
    "BidIdentityView",
    "BidIdentityCorrection",
    "CorrectBidIdentity",
    "RegisterBidder",
    "RegisteredBiddingCompanyView",
    "SelectDefaultBidder",
    "ResearchCapabilities",
    "ResearchHistoryPage",
    "ResearchOutcomePage",
    "ResearchOutcomeDetail",
    "ResearchSamplePage",
    "ResearchGroupingView",
    "ResearchGroupingRequest",
    "AuthoringDocument",
    "AuthoringNode",
    "AuthoringCapabilities",
    "AuthoringDraftPage",
    "AuthoringRevisionPage",
    "AuthoringPreviewRequest",
    "AuthoringPreview",
    "AuthoringSnapshot",
    "AuthoringSectionAdded",
    "EvidenceAuthoringReferenceView",
    "CreateDraftRequest",
    "DraftView",
    "SectionView",
    "SaveDraft",
    "DraftSaved",
    "BuildRequest",
    "BuildView",
    "BuildPage",
    "BatchRuleConfirmation",
    "RuleDecisionRequest",
    "FactDecisionRequest",
    "FindingDecisionRequest",
    "RestoreAuthoringSection",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalHit",
    "ReviewStateView",
    "ReviewPage",
    "ReviewManifest",
    "ReviewView",
    "RuleSetView",
    "FactSetView",
    "FindingPage",
    "MatrixView",
    "SettingsView",
    "DecisionRequest",
    "BatchRuleConfirmationRequest",
    "BatchRuleConfirmationView",
    "FactChangeRequest",
    "Cancellation",
    "DiagnosticPage",
    "ReviewRequest",
    "RuleSetCorrectionRequest",
    "DocumentEvidenceView",
    "DownloadGrant",
    "EvidenceView",
    "AuthView",
    "Bid",
    "BidPage",
    "CreateBid",
    "UploadGrant",
    "UploadPartsView",
    "RefreshUploadRequest",
    "UploadRequest",
    "UploadView",
    "UploadBatchView",
    "UploadBindingView",
    "UploadBindingRequest",
    "BatchCommit",
    "MaterialView",
    "DocumentView",
    "Accepted",
    "Operation",
    "UploadComplete",
    "UploadTarget",
]
if args.schemas:
    wanted = args.schemas.split(",")
seen = set()


def render(s):
    if "$ref" in s:
        name = s["$ref"].split("/")[-1]
        if name not in wanted:
            wanted.append(name)
        return name
    if "const" in s:
        return json.dumps(s["const"], ensure_ascii=False)
    if "enum" in s:
        return " | ".join(json.dumps(x, ensure_ascii=False) for x in s["enum"])
    for union in ["oneOf", "anyOf"]:
        if union in s:
            return "(" + " | ".join(render(x) for x in s[union]) + ")"
    typ = s.get("type")
    if isinstance(typ, list):
        return " | ".join(render(dict(s, type=x)) for x in typ)
    if typ == "object" and not s.get("properties"):
        if s.get("additionalProperties") is False:
            return "Record<string, never>"
        value_type = (
            render(s["additionalProperties"])
            if isinstance(s.get("additionalProperties"), dict)
            else "unknown"
        )
        return "Record<string, " + value_type + ">"
    if typ == "object":
        req = s.get("required", [])
        return (
            "{\n"
            + "".join(
                f"  {json.dumps(k)}" + ("" if k in req else "?") + ": " + render(v) + ";\n"
                for k, v in s.get("properties", {}).items()
            )
            + "}"
        )
    if typ == "array":
        return "(" + render(s["items"]) + ")[]"
    return {
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
        "string": "string",
    }.get(typ, "unknown")


source = (
    "packages/contracts/implemented.openapi.json"
    if args.openapi == root / "packages/contracts/implemented.openapi.json"
    else "implementation source preview: " + str(args.openapi)
)
lines = [f"// Generated from {source}. Run python scripts/generate-contracts.py.\n"]
for name in wanted:
    if name not in seen:
        seen.add(name)
        lines.append(f"export type {name} = {render(schemas[name])};\n")
args.output.write_text("\n".join(lines))
if args.schemas:
    raise SystemExit(0)

operations = [
    f"{method.upper()} {path}"
    for path, methods in contract["paths"].items()
    for method in methods
    if method in {"get", "post", "patch", "put", "delete"}
]
with args.output.open("a") as output:
    output.write(
        "\nexport const implementedOperations: readonly string[] = "
        + json.dumps(operations, ensure_ascii=False)
        + ";\n"
    )

with args.output.open("a") as output:
    batch_limit = schemas["BatchRuleConfirmation"]["properties"]["rule_ids"]["maxItems"]
    output.write(f"\nexport const ruleConfirmationLimit: number = {batch_limit};\n")
