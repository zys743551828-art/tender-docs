"""人工决定实际协议增量；只覆盖已经注册的处理器。"""

from copy import deepcopy


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]

    def ref(name):
        return {"$ref": "#/components/schemas/" + name}

    def obj(properties):
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(properties),
        }

    uuid = {"type": "string", "format": "uuid"}
    schemas["FactDecisionCarryover"] = obj(
        {
            "from_fact_id": uuid,
            "from_decision_id": uuid,
            "equivalence_digest": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "adopted_at": {"type": "string", "format": "date-time"},
        }
    )
    extras = {
        "carryover": {"anyOf": [ref("FactDecisionCarryover"), {"type": "null"}]},
        "decision": {"anyOf": [ref("DecisionView"), {"type": "null"}]},
        "source_validity": {"enum": ["effective", "needs_revalidation"]},
        "source_issues": {"type": "array", "items": {"type": "string"}},
    }
    schemas["FactView"]["properties"].update(extras)
    schemas["FactView"]["required"] += list(extras)
    for kind, values in [
        ("Rule", ["confirmed", "rejected", "deferred"]),
        ("Fact", ["confirmed", "rejected"]),
        ("Finding", ["confirm", "dismiss", "defer", "reopen"]),
    ]:
        model = deepcopy(schemas["DecisionRequest"])
        model["properties"]["decision"] = {"enum": values}
        model["properties"]["reason"].update(maxLength=2000)
        schemas[kind + "DecisionRequest"] = model
        path = f"/api/v1/{kind.lower()}s/{{{kind.lower()}_id}}/decisions"
        if path in actual["paths"]:
            actual["paths"][path]["post"]["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": ref(kind + "DecisionRequest")}},
            }
    schemas["BatchRuleConfirmation"] = obj(
        {
            "base_rule_set_id": uuid,
            "rule_ids": {
                "type": "array",
                "items": uuid,
                "minItems": 1,
                "maxItems": 500,
                "uniqueItems": True,
            },
            "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
        }
    )
    specs = [
        ("/api/v1/facts/{fact_id}", "get", "getFact", None, "FactView"),
        (
            "/api/v1/bids/{bid_id}/rules/decisions",
            "post",
            "confirmSelectedRules",
            "BatchRuleConfirmation",
            "RuleSetView",
        ),
    ]
    for path, method, op_id, request_model, response_model in specs:
        if path not in actual["paths"]:
            continue
        op = actual["paths"][path][method]
        op.update(operationId=op_id, security=[{"SessionCookie": []}])
        if request_model:
            op["parameters"] += [
                {"$ref": "#/components/parameters/" + key}
                for key in ("IdempotencyKey", "CSRF", "IfMatch")
            ]
            op["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": ref(request_model)}},
            }
        op["responses"] = {
            "200": {
                "description": "已实现的人工核对资源",
                "headers": {"ETag": {"schema": {"type": "string"}}},
                "content": {"application/json": {"schema": ref(response_model)}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
