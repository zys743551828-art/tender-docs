"""给actual OpenAPI补齐已注册的研究路径，不扩大目标实现范围。"""

from copy import deepcopy

from packages.research.profile import descriptor, digest


def obj(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


def arr(item):
    return {"type": "array", "items": item}


def extend_openapi(actual):
    from apps.api.research_routes import (
        DatasetRequest,
        GroupingRequest,
        HistoricalImportRequest,
        OutcomeVerification,
    )

    schemas = actual["components"]["schemas"]
    uuid = {"type": "string", "format": "uuid"}
    integer = {"type": "integer", "minimum": 0}
    string = {"type": "string"}
    nullable_uuid = {"type": ["string", "null"], "format": "uuid"}
    digest_schema = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    boolean = {"type": "boolean"}
    empty = obj({})
    schemas["ResearchOutcomeVerification"] = OutcomeVerification.model_json_schema()
    schemas["ResearchDatasetRequest"] = DatasetRequest.model_json_schema()
    schemas["ResearchOutcomeContext"] = obj(
        {
            "bid_id": uuid,
            "submission_id": uuid,
            "identity": obj(
                {
                    "project_id": nullable_uuid,
                    "round_id": nullable_uuid,
                    "bidder_id": nullable_uuid,
                    "body_version_id": uuid,
                }
            ),
            "display": obj(
                {
                    "project_title": string,
                    "lot": {"type": ["string", "null"]},
                    "round_no": {"type": ["integer", "null"], "minimum": 1},
                    "bidder_name": string,
                    "body_filename": string,
                }
            ),
            "submitted_at": {"type": "string", "format": "date-time"},
            "kind": {"enum": ["standard", "offline_fact"]},
            "material_snapshot_id": nullable_uuid,
            "material_is_frozen": boolean,
            "material_candidates": arr(
                obj(
                    {
                        "id": uuid,
                        "revision": integer,
                        "created_at": {"type": "string", "format": "date-time"},
                    }
                )
            ),
            "source_candidates": arr(
                obj(
                    {
                        "id": uuid,
                        "original_name": string,
                        "obtained_at": {"type": "string", "format": "date-time"},
                    }
                )
            ),
            "identity_ready": boolean,
            "identity_status": {"enum": ["confirmed", "unavailable", "changed"]},
            "identity_reason": {"type": ["string", "null"]},
        }
    )
    import_schema = HistoricalImportRequest.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    schemas.update(import_schema.pop("$defs", {}))
    schemas["HistoricalImportRequest"] = import_schema
    schemas["HistoricalImportBatch"] = obj(
        {
            "id": uuid,
            "mode": {"enum": ["dry_run", "confirmed_import"]},
            "revision": {"type": "integer", "minimum": 1},
            "manifest_blob_id": uuid,
            "records": arr(
                obj(
                    {
                        "batch_id": uuid,
                        "external_record_id": string,
                        "source_digest": digest_schema,
                        "state": {"enum": ["pending", "imported", "conflict", "failed"]},
                        "bid_id": nullable_uuid,
                        "errors": arr(string),
                    }
                )
            ),
            "next_offset": {"type": ["integer", "null"]},
            "counts": obj(
                {"total": integer, "pending": integer, "imported": integer, "errors": integer}
            ),
            "formal_sample_admission": {"const": "separate_verified_technical_label_check"},
            "standard_submission_gate_checked": {"const": False},
        }
    )
    schemas["ResearchGroupingRequest"] = GroupingRequest.model_json_schema()
    schemas["ResearchGroupingView"] = obj(
        {
            "sample_id": uuid,
            "source_group_id": uuid,
            "revision": integer,
            "metadata": obj(
                {
                    "template_key": {"type": ["string", "null"]},
                    "near_duplicate_key": {"type": ["string", "null"]},
                    "reviewed": boolean,
                    "reason": string,
                }
            ),
            "related_sample_ids": arr(uuid),
            "eligible_label_pair": boolean,
        }
    )
    outcome = deepcopy(schemas["OutcomeView"])
    outcome["properties"].update(
        {
            "verification_revision": integer,
            "supersedes_id": nullable_uuid,
            "verification_details": {
                "anyOf": [ref("ResearchOutcomeVerification"), {"type": "null"}]
            },
        }
    )
    outcome["required"] += ["verification_revision", "supersedes_id", "verification_details"]
    schemas["ResearchOutcomeView"] = outcome
    detail = deepcopy(outcome)
    detail["properties"]["current"] = boolean
    detail["required"].append("current")
    schemas["ResearchOutcomeDetail"] = detail
    schemas["ResearchOutcomePage"] = obj(
        {
            "items": arr(ref("ResearchOutcomeView")),
            "heads": arr(
                obj(
                    {"submission_id": uuid, "bid_id": uuid, "outcome_id": uuid, "revision": integer}
                )
            ),
        }
    )
    sample = obj(
        {
            "id": uuid,
            "bid_id": uuid,
            "submission_id": uuid,
            "outcome_id": uuid,
            "source_group_id": uuid,
            "known_at": {"type": "string", "format": "date-time"},
            "admission": {"enum": ["pending", "admitted", "rejected"]},
            "reasons": arr(string),
        }
    )
    schemas["ResearchSample"] = sample
    admitted = deepcopy(sample)
    admitted["properties"]["eligible_label_pair"] = boolean
    admitted["required"].append("eligible_label_pair")
    schemas["ResearchAdmission"] = admitted
    listed = deepcopy(sample)
    listed["properties"]["current"] = boolean
    listed["required"].append("current")
    schemas["ResearchListedSample"] = listed
    next_offset = {"type": ["integer", "null"], "minimum": 0}
    schemas["ResearchSamplePage"] = obj(
        {
            "items": arr(ref("ResearchListedSample")),
            "next_offset": next_offset,
            "formal_model_eligible": {"const": False},
        }
    )
    schemas["ResearchHistoryPage"] = obj(
        {
            "items": arr(
                obj(
                    {
                        "id": uuid,
                        "title": string,
                        "created_at": {"type": "string", "format": "date-time"},
                        "submission_count": integer,
                        "outcome_count": integer,
                        "admitted_samples": integer,
                    }
                )
            ),
            "next_offset": next_offset,
            "read_only": {"const": True},
        }
    )
    schemas["ResearchDatasetFrozen"] = obj(
        {
            "dataset_manifest_blob_id": uuid,
            "split_manifest_blob_id": uuid,
            "sample_ids": arr(uuid),
            "dataset_digest": digest_schema,
            "split_digest": digest_schema,
            "formal_model_eligible": {"const": False},
        }
    )
    schemas["ResearchChapterPackages"] = obj(
        {
            "review_id": uuid,
            "packages": arr(
                obj(
                    {
                        "id": uuid,
                        "rule_id": uuid,
                        "manifest_blob_id": uuid,
                        "digest": digest_schema,
                        "required_ranges": arr(string),
                        "read_ranges": arr(string),
                        "unread_ranges": arr(string),
                    }
                )
            ),
            "method_digest": digest_schema,
            "scope": {"const": "full_body"},
            "formal_prediction_mode": {"const": "unavailable"},
        }
    )
    schemas["ResearchActualRunItems"] = obj(
        {"items": arr(ref("ResearchRunItem")), "next_offset": next_offset}
    )
    schemas["ResearchCapabilities"] = obj(
        {
            "historical_outcomes": boolean,
            "official_zero_is_valid": boolean,
            "unknown_is_not_zero": boolean,
            "batch_dry_run": boolean,
            "price": obj(
                {
                    "available": boolean,
                    "digest": {"type": ["string", "null"]},
                    "currency": {"type": ["string", "null"]},
                }
            ),
            "formal_prediction": obj({"mode": {"const": "unavailable"}, "reasons": arr(string)}),
            "model_training": {"const": "awaiting_verified_real_labels"},
            "model_release": {"const": "unavailable"},
            "requires_full_chapters": {"const": True},
            "judgments_per_item": {"const": 3},
            "source_authority_configured": boolean,
        }
    )
    mappings = [
        (
            "/bids/{bid_id}/outcome-context",
            "get",
            "getOutcomeRegistrationContext",
            None,
            "ResearchOutcomeContext",
            200,
            False,
        ),
        (
            "/research/import-previews",
            "post",
            "previewHistoricalImport",
            "HistoricalImportRequest",
            "HistoricalImportBatch",
            201,
            False,
        ),
        (
            "/research/imports/{batch_id}",
            "get",
            "getHistoricalImport",
            None,
            "HistoricalImportBatch",
            200,
            False,
        ),
        (
            "/research/imports/{batch_id}/confirm",
            "post",
            "confirmHistoricalImport",
            "ResearchEmptyRequest",
            "HistoricalImportBatch",
            200,
            True,
        ),
        (
            "/research/samples/{sample_id}/grouping",
            "get",
            "getSampleGrouping",
            None,
            "ResearchGroupingView",
            200,
            False,
        ),
        (
            "/research/samples/{sample_id}/grouping",
            "post",
            "recordSampleGrouping",
            "ResearchGroupingRequest",
            "ResearchGroupingView",
            200,
            True,
        ),
        (
            "/research/capabilities",
            "get",
            "getResearchCapabilities",
            None,
            "ResearchCapabilities",
            200,
            False,
        ),
        ("/history", "get", "searchHistoricalBids", None, "ResearchHistoryPage", 200, False),
        (
            "/bids/{bid_id}/outcomes",
            "get",
            "listBidOutcomes",
            None,
            "ResearchOutcomePage",
            200,
            False,
        ),
        (
            "/bids/{bid_id}/outcomes",
            "post",
            "registerOfficialOutcome",
            "OutcomeRequest",
            "ResearchOutcomeView",
            201,
            False,
        ),
        (
            "/outcomes/{outcome_id}",
            "get",
            "getOfficialOutcome",
            None,
            "ResearchOutcomeDetail",
            200,
            False,
        ),
        (
            "/outcomes/{outcome_id}/corrections",
            "post",
            "correctOfficialOutcome",
            "OutcomeRequest",
            "ResearchOutcomeView",
            201,
            True,
        ),
        (
            "/outcomes/{outcome_id}/verification",
            "post",
            "verifyOfficialOutcome",
            "ResearchOutcomeVerification",
            "ResearchOutcomeView",
            200,
            True,
        ),
        (
            "/bids/{bid_id}/submissions/{submission_id}/sample-admission",
            "post",
            "checkSampleAdmission",
            "ResearchEmptyRequest",
            "ResearchAdmission",
            200,
            False,
        ),
        ("/research/samples", "get", "listResearchSamples", None, "ResearchSamplePage", 200, False),
        (
            "/research/datasets",
            "post",
            "freezeResearchDataset",
            "ResearchDatasetRequest",
            "ResearchDatasetFrozen",
            201,
            False,
        ),
        (
            "/research/reviews/{review_id}/chapter-packages",
            "post",
            "prepareResearchChapters",
            "ResearchEmptyRequest",
            "ResearchChapterPackages",
            201,
            False,
        ),
        (
            "/research/previews",
            "post",
            "previewResearchRun",
            "ResearchPreviewRequest",
            "ResearchPreviewView",
            201,
            False,
        ),
        (
            "/research/runs",
            "post",
            "startResearchRun",
            "ResearchRunRequest",
            "ResearchRunView",
            202,
            False,
        ),
        ("/research/runs/{run_id}", "get", "getResearchRun", None, "ResearchRunView", 200, False),
        (
            "/research/runs/{run_id}/items",
            "get",
            "listResearchRunItems",
            None,
            "ResearchActualRunItems",
            200,
            False,
        ),
        (
            "/research/runs/{run_id}/resume",
            "post",
            "resumeResearchRun",
            "ResumeResearchRequest",
            "ResearchRunView",
            200,
            True,
        ),
        (
            "/research/runs/{run_id}/cancel",
            "post",
            "cancelResearchRun",
            "ReasonRequest",
            "ResearchRunView",
            200,
            True,
        ),
    ]
    schemas["ResearchEmptyRequest"] = empty
    for (
        path,
        method,
        operation_id,
        request_schema,
        response_schema,
        status,
        conditional,
    ) in mappings:
        operation = actual["paths"].get("/api/v1" + path, {}).get(method)
        if operation is None:
            continue
        operation["operationId"] = operation_id
        operation["security"] = [{"SessionCookie": []}]
        operation["parameters"] = [
            p for p in operation.get("parameters", []) if p.get("in") != "header"
        ]
        if method == "post":
            operation["parameters"] += [
                {"name": name, "in": "header", "required": True, "schema": string}
                for name in ["X-CSRF-Token", "Idempotency-Key"]
                + (["If-Match"] if conditional else [])
            ]
        if request_schema:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": ref(request_schema)}},
            }
        operation["responses"] = {
            str(status): {
                "description": "实际研究工程投影；不授予模型业务资格",
                "content": {"application/json": {"schema": ref(response_schema)}},
                "headers": {
                    "ETag": {"schema": string},
                    "Verification-ETag": {"schema": string},
                    "Outcome-Head-ETag": {"schema": string},
                },
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
    actual.setdefault("x-research-runtime", {}).update(
        {"profile": descriptor(), "profile_digest": digest(), "formal_scoring_eligible": False}
    )
