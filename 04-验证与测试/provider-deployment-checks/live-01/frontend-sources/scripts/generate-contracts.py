"""Generate only schema types consumed by this UI, without inventing API routes."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[3]
contract = json.loads((root / "packages/contracts/implemented.openapi.json").read_text())
schemas = contract["components"]["schemas"]
wanted = [
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalHit",
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


lines = [
    "// Generated from packages/contracts/implemented.openapi.json. Run python scripts/generate-contracts.py.\n"
]
for name in wanted:
    if name not in seen:
        seen.add(name)
        lines.append(f"export type {name} = {render(schemas[name])};\n")
(root / "apps/web/src/contracts.ts").write_text("\n".join(lines))

operations = [
    f"{method.upper()} {path}"
    for path, methods in contract["paths"].items()
    for method in methods
    if method in {"get", "post", "patch", "put", "delete"}
]
with (root / "apps/web/src/contracts.ts").open("a") as output:
    output.write(
        "\nexport const implementedOperations: readonly string[] = "
        + json.dumps(operations, ensure_ascii=False)
        + ";\n"
    )
