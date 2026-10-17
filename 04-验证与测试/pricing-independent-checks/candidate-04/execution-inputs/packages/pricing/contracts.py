"""Only extend operations actually registered by pricing_routes; frozen target remains untouched."""

from copy import deepcopy

from pydantic import BaseModel

from packages.pricing.models import PricingRequest, RegisterFormula, SavePlan


def obj(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


def arr(item, **kwargs):
    return {"type": "array", "items": item, **kwargs}


U = {"type": "string", "format": "uuid"}
S = {"type": "string"}
N = {"type": "integer", "minimum": 0}
INTEGER = {"type": "integer", "minimum": 1}
D = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
T = {"type": "string", "format": "date-time"}
NULL_TEXT = {"type": ["string", "null"]}
NULL_UUID = {"type": ["string", "null"], "format": "uuid"}
B = {"type": "boolean"}


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]
    input_models: list[tuple[str, type[BaseModel]]] = [
        ("PricingRegisterFormula", RegisterFormula),
        ("PricingRequest", PricingRequest),
        ("PricingSavePlan", SavePlan),
    ]
    for name, model in input_models:
        schema = model.model_json_schema(ref_template="#/components/schemas/Pricing{model}")
        for nested, definition in schema.pop("$defs", {}).items():
            schemas["Pricing" + nested] = definition
        schemas[name] = schema
    state = obj({"applicable": B, "reasons": arr(S)})
    schemas["PricingApplicability"] = state
    scope = obj(
        {
            "bid_id": U,
            "round_id": U,
            "effective_documents": arr(
                obj(
                    {
                        "role": {"enum": ["tender", "supplement"]},
                        "document_version_id": U,
                        "digest": D,
                    }
                )
            ),
        }
    )
    head = obj(
        {
            "source_id": U,
            "id": U,
            "document_version_id": U,
            "kind": {"enum": ["document"]},
            "policy_class": {"const": "prebid_content"},
            "known_at": T,
            "digest": D,
            "revision": INTEGER,
            "status": {"enum": ["published", "withdrawn", "candidate"]},
            "valid_from": T,
            "valid_until": {"type": ["string", "null"], "format": "date-time"},
            "bid_id": U,
        }
    )
    calculator = obj({"name": S, "version": INTEGER, "implementation_sha256": D})
    request = deepcopy(schemas["PricingRegisterFormula"]["properties"])
    manifest = obj(
        {
            "schema": {"const": "pricing-formula/1"},
            "formula_version_id": U,
            "bid_id": U,
            "material_snapshot_id": NULL_UUID,
            "material_revision": INTEGER,
            "applicability_scope": scope,
            **request,
            "confirmed_by": U,
            "confirmed_at": T,
            "calculator": calculator,
            "source_authority": obj({"scope": scope, "ancestor_heads": arr(head)}),
        }
    )
    schemas["PricingFormulaManifest"] = manifest
    schemas["PricingFormulaView"] = obj(
        {
            "formula_version_id": U,
            "formula_digest": D,
            "formula": ref("PricingFormulaManifest"),
            "current_applicability": ref("PricingApplicability"),
        }
    )
    summary = obj(
        {
            "formula_version_id": U,
            "formula_digest": D,
            "title": S,
            "method": S,
            "maximum": S,
            "amount_unit": S,
            "currency": NULL_TEXT,
            "rounding_policy": S,
            "confirmed_at": T,
            "confirmed_by": U,
            "current_applicability": ref("PricingApplicability"),
        }
    )
    schemas["PricingFormulaPage"] = obj({"items": arr(summary), "next_cursor": NULL_UUID})
    schemas["PricingContext"] = obj(
        {
            "bid_id": U,
            "material_snapshot_id": NULL_UUID,
            "material_revision": INTEGER,
            "scope": scope,
            "parses": arr(obj({"id": U, "document_version_id": U})),
            "source_heads": arr(
                obj(
                    {
                        "id": U,
                        "digest": D,
                        "revision": {"type": ["integer", "null"]},
                        "status": NULL_TEXT,
                        "valid_from": {"type": ["string", "null"], "format": "date-time"},
                        "valid_until": {"type": ["string", "null"], "format": "date-time"},
                    }
                )
            ),
        }
    )
    schemas["PricingFormulaSourcePage"] = obj(
        {
            "items": arr(
                obj(
                    {
                        "source_id": U,
                        "source_revision": {"type": ["integer", "null"]},
                        "document_version_id": U,
                        "parse_id": U,
                        "physical_page": INTEGER,
                        "evidence_id": U,
                        "filename": S,
                        "quote": S,
                        "source_registered": B,
                    }
                )
            ),
            "next_cursor": NULL_UUID,
        }
    )
    schemas["PricePoint"] = obj(
        {
            "price": S,
            "score": NULL_TEXT,
            "eligible": B,
            "notes": arr(S),
            "baseline_price": NULL_TEXT,
            "valid_price_count": N,
            "calculation_steps": arr(S),
        }
    )
    frozen = obj(
        {
            "id": U,
            "formula_version_id": U,
            "formula_digest": D,
            "input_digest": D,
            "formula_evidence_ids": arr(U),
            "price_score": NULL_TEXT,
            "eligible": B,
            "notes": arr(S),
            "candidate_price": S,
            "maximum": S,
            "baseline_price": NULL_TEXT,
            "valid_price_count": N,
            "candidates": arr(ref("PricePoint")),
            "curve": arr(ref("PricePoint")),
            "calculation_steps": arr(S),
            "assumptions": arr(S),
            "technical_exchange_note": S,
            "excluded_competitor_assumptions": arr(obj({"index": N, "price": S, "reason": S})),
        }
    )
    schemas["PricingFrozenResult"] = frozen
    schemas["PricingSimulationView"] = obj(
        {
            **deepcopy(frozen["properties"]),
            "input": ref("PricingRequest"),
            "formula_snapshot": ref("PricingFormulaManifest"),
            "created_by": U,
            "created_at": T,
            "current_applicability": ref("PricingApplicability"),
        }
    )
    schemas["PricingPlanView"] = obj(
        {
            "id": U,
            "simulation_id": U,
            "formula_version_id": U,
            "formula_digest": D,
            "input_digest": D,
            "input": ref("PricingRequest"),
            "formula_snapshot": ref("PricingFormulaManifest"),
            "result": ref("PricingFrozenResult"),
            "reason": S,
            "created_by": U,
            "created_at": T,
            "current_applicability": ref("PricingApplicability"),
        }
    )
    schemas["PricingPlanPage"] = obj(
        {"items": arr(ref("PricingPlanView")), "next_cursor": NULL_UUID}
    )
    routes = [
        ("/pricing-context", "get", "getPricingContext", None, "PricingContext", 200),
        (
            "/pricing-formula-sources",
            "get",
            "listPricingFormulaSources",
            None,
            "PricingFormulaSourcePage",
            200,
        ),
        (
            "/pricing-formulas",
            "post",
            "registerPricingFormula",
            "PricingRegisterFormula",
            "PricingFormulaView",
            201,
        ),
        ("/pricing-formulas", "get", "listPricingFormulas", None, "PricingFormulaPage", 200),
        (
            "/pricing-formulas/{formula_version_id}",
            "get",
            "getPricingFormula",
            None,
            "PricingFormulaView",
            200,
        ),
        (
            "/pricing-simulations",
            "post",
            "simulatePricing",
            "PricingRequest",
            "PricingSimulationView",
            201,
        ),
        (
            "/pricing-simulations/{simulation_id}",
            "get",
            "getPricingSimulation",
            None,
            "PricingSimulationView",
            200,
        ),
        ("/pricing-plans", "post", "savePricingPlan", "PricingSavePlan", "PricingPlanView", 201),
        ("/pricing-plans", "get", "listPricingPlans", None, "PricingPlanPage", 200),
        ("/pricing-plans/{plan_id}", "get", "getPricingPlan", None, "PricingPlanView", 200),
    ]
    for suffix, method, operation_id, request, response, status in routes:
        operation = actual["paths"].get("/api/v1/bids/{bid_id}" + suffix, {}).get(method)
        if operation is None:
            continue
        operation["operationId"] = operation_id
        operation["security"] = [{"SessionCookie": []}]
        operation["parameters"] = [
            p for p in operation.get("parameters", []) if p.get("in") != "header"
        ]
        if method == "post":
            operation["parameters"] += [
                {"name": name, "in": "header", "required": True, "schema": S}
                for name in ("X-CSRF-Token", "Idempotency-Key", "If-Match")
            ]
        if request:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": ref(request)}},
            }
        operation["responses"] = {
            str(status): {
                "description": "Actual implemented pricing resource",
                **(
                    {"headers": {"ETag": {"schema": S}}}
                    if response in {"PricingContext", "PricingFormulaView", "PricingSimulationView"}
                    else {}
                ),
                "content": {"application/json": {"schema": ref(response)}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
    return actual
