"""报告实际只读身份和受理契约；仅注册真实已实现路由。"""

from apps.api.submission_contracts import NULL_UUID, TEXT, UUID, obj, page


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]
    policy = {
        "artifact_kind": {"enum": ["review_report", "annotated_review_copy"]},
        "format": {"enum": ["docx", "pdf"]},
    }
    revisions = {
        "presentation_revision": {"type": "integer", "minimum": 1},
        "source_impact_revision": {"type": "integer", "minimum": 1},
    }
    schemas["ReportContextView"] = obj(
        {
            "review_id": UUID,
            "body_version_id": UUID,
            "original_name": TEXT,
            **revisions,
            "context_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "current": {"type": "boolean"},
            "source_state": TEXT,
            "supported": {"type": "array", "items": obj(policy)},
        }
    )
    schemas["ReportExportView"] = obj(
        {
            "id": UUID,
            "review_id": UUID,
            "operation_id": UUID,
            "state": {
                "enum": [
                    "queued",
                    "running",
                    "cancel_requested",
                    "succeeded",
                    "failed",
                    "cancelled",
                ]
            },
            **policy,
            **revisions,
            "document_version_id": NULL_UUID,
            "gaps": {"type": "array", "items": TEXT},
            "error_code": {"type": ["string", "null"]},
        }
    )
    schemas["ReportExportPage"] = page("ReportExportView")
    for path, method, name, response in (
        ("/reviews/{review_id}/report-context", "get", "getReportContext", "ReportContextView"),
        ("/report-exports/{report_id}", "get", "getReportExport", "ReportExportView"),
        ("/reviews/{review_id}/report-exports", "get", "listReportExports", "ReportExportPage"),
        ("/reviews/{review_id}/report-exports", "post", "createReport", "Accepted"),
    ):
        operation = actual["paths"].get("/api/v1" + path, {}).get(method)
        if not operation:
            continue
        operation.update(operationId=name, security=[{"SessionCookie": []}])
        operation["responses"] = {
            "202" if method == "post" else "200": {
                "description": "冻结当前政策的独立展示副本；生成不调用模型，不授正文资格",
                "headers": {"ETag": {"schema": TEXT}},
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + response}}
                },
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
        if method == "post":
            operation["description"] = (
                "先读取GET report-context的实际强ETag和展示/来源修订；批注副本仅PDF，报告支持Word/PDF；只受理，完成后GET report-exports返回真实版本，再取下载授权。"
            )
