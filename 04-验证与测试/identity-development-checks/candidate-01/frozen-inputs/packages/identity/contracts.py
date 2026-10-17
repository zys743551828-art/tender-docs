"""Actual identity selection operations; the reviewed target snapshot stays immutable."""

from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

from packages.domain.runtime import DomainError

ID = {"type": "string", "format": "uuid"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 1000, "pattern": r"\S"}


def obj(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


SCHEMAS = {
    "RegisterBidder": obj(
        {"name": TEXT | {"maxLength": 300}, "reason": TEXT, "set_as_default": {"type": "boolean"}},
        ["name", "reason"],
    ),
    "SelectDefaultBidder": obj({"bidder_id": ID}),
    "CorrectBidIdentity": obj(
        {"bidder_id": ID, "round_id": ID, "reason": TEXT}, ["bidder_id", "reason"]
    ),
}


def validate(name, value):
    errors = list(
        Draft202012Validator(SCHEMAS[name], format_checker=FormatChecker()).iter_errors(value)
    )
    if errors:
        raise DomainError(
            "INVALID_REQUEST",
            "请完整填写真实身份及选择依据",
            422,
            {"fields": [".".join(map(str, e.absolute_path)) for e in errors]},
        )


def extend_openapi(actual):
    actual["components"]["schemas"].update(deepcopy(SCHEMAS))
    operations = {
        ("/identity/bidders", "get"): ("listBiddingCompanies", None),
        ("/identity/bidders", "post"): ("registerBiddingCompany", "RegisterBidder"),
        ("/identity/default-bidder", "get"): ("getDefaultBiddingCompany", None),
        ("/identity/default-bidder", "put"): ("selectDefaultBiddingCompany", "SelectDefaultBidder"),
        ("/identity/engineering-projects", "get"): ("listEngineeringProjects", None),
        ("/identity/rounds", "get"): ("listEngineeringRounds", None),
        ("/bids/{bid_id}/identity", "get"): ("getBidIdentity", None),
        ("/bids/{bid_id}/identity", "put"): ("correctBidIdentity", "CorrectBidIdentity"),
    }
    for (path, method), (operation_id, schema) in operations.items():
        op = actual["paths"].get("/api/v1" + path, {}).get(method)
        if op is None:
            continue
        op["operationId"] = operation_id
        op["security"] = [{"SessionCookie": []}]
        if schema:
            op["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + schema}}
                },
            }
            op.setdefault("parameters", []).extend(
                [
                    {
                        "in": "header",
                        "name": "Idempotency-Key",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "in": "header",
                        "name": "X-CSRF-Token",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "in": "header",
                        "name": "Origin",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ]
            )
            if method == "put":
                op["parameters"].append(
                    {
                        "in": "header",
                        "name": "If-Match",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                )
        op["responses"] = {
            "201" if operation_id == "registerBiddingCompany" else "200": {
                "description": "实际身份选择结果"
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
