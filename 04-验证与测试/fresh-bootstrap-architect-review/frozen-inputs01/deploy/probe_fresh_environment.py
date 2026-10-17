"""Read identity/capability boundaries of the specifically created combination environment."""

import argparse
import json
import os
from pathlib import Path

import psycopg

from deploy.bootstrap_fresh import ROOT, business_configuration, private_configuration
from deploy.initialize_database import file_configuration


def require(condition):
    if not condition:
        raise RuntimeError("FRESH_ENVIRONMENT_PROBE_FAILED")


def scalar(c, statement, params=()):
    row = c.execute(statement, params).fetchone()
    if row is None:
        raise RuntimeError("PROBE_QUERY_MISSING")
    return row[0]


def denied(dsn, statement):
    try:
        with psycopg.connect(dsn) as c:
            c.execute(statement)
    except psycopg.errors.InsufficientPrivilege:
        return True
    raise RuntimeError("EXPECTED_PERMISSION_DENIAL")


def probe(receipt_path):
    receipt = json.loads(Path(receipt_path).read_text())
    init = private_configuration(ROOT / receipt["profiles"]["init"])
    business = file_configuration(ROOT / receipt["profiles"]["business"])
    require(business == business_configuration(init))
    require(
        not {
            "OWNER_DATABASE_DSN",
            "POSTGRES_PASSWORD",
            "MODEL_TRAIN_DATABASE_DSN",
            "MODEL_EVALUATE_DATABASE_DSN",
        }
        & business.keys()
    )
    groups = (
        "tender_api",
        "tender_worker",
        "tender_research_worker",
        "tender_research_read",
        "tender_score_read",
        "tender_model_train",
        "tender_model_evaluate",
        "tender_model_score",
    )
    roles = {}
    for role, login in receipt["logins"].items():
        key = role.upper() + "_DATABASE_DSN"
        if role.startswith("model_"):
            purpose = file_configuration(ROOT / receipt["profiles"][role])
            require(set(purpose) == {key})
            dsn = purpose[key]
        else:
            dsn = business[key]
        with psycopg.connect(dsn) as c:
            row = c.execute(
                "SELECT current_database(),current_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if row is None:
                raise RuntimeError("IDENTITY_QUERY_MISSING")
            require(row[0] == receipt["database"] and row[1] == login and (not any(row[2:])))
            require(
                not scalar(
                    c,
                    "SELECT EXISTS(SELECT FROM pg_class WHERE relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND relnamespace IN(to_regnamespace('design'),to_regnamespace('retrieval'),to_regnamespace('model_governance')))",
                )
            )
            membership = {
                group: scalar(c, "SELECT pg_has_role(current_user,%s,'MEMBER')", (group,))
                for group in groups
            }
            expected = {"tender_" + role}
            require({group for group, member in membership.items() if member} == expected)
            roles[role] = {
                "login": login,
                "nonowner": True,
                "restricted_flags": True,
                "capabilities": [g for g, v in membership.items() if v],
            }
    api = business["API_DATABASE_DSN"]
    with psycopg.connect(api) as c:
        require(scalar(c, "SELECT count(*) FROM design.bids") == 0)
        require(scalar(c, "SELECT count(*) FROM model_governance.jobs") == 0)
        require(scalar(c, "SELECT count(*) FROM retrieval.knowledge_indexes") == 0)
        c.execute(
            "INSERT INTO design.members(id,display_name) VALUES(gen_random_uuid(),'rollback capability probe')"
        )
        c.rollback()
    require(
        denied(business["WORKER_DATABASE_DSN"], "UPDATE design.bids SET title=title WHERE false")
    )
    require(denied(business["WORKER_DATABASE_DSN"], "SELECT * FROM model_governance.exports"))
    require(
        denied(
            business["SCORE_READ_DATABASE_DSN"], "UPDATE design.bids SET title=title WHERE false"
        )
    )
    for role, forbidden in (("model_train", "read_final"), ("model_evaluate", "read_training")):
        dsn = init[role.upper() + "_DATABASE_DSN"]
        require(denied(dsn, "SELECT * FROM design.samples"))
        require(denied(dsn, "SELECT * FROM model_governance.exports"))
        require(
            denied(
                dsn,
                f"SELECT model_governance.{forbidden}('00000000-0000-0000-0000-000000000000','00000000-0000-0000-0000-000000000000',0)",
            )
        )
        permitted = "read_training" if role == "model_train" else "read_final"
        with psycopg.connect(dsn) as c:
            require(
                scalar(
                    c,
                    "SELECT has_function_privilege(current_user,%s,'EXECUTE')",
                    (f"model_governance.{permitted}(uuid,uuid,bigint)",),
                )
            )
    os.environ.update(business)
    from packages.domain.providers.oss import OssClient

    native = OssClient.from_env()
    require(native.prefix == receipt["object_store_prefix"] == business["OBJECT_STORE_PREFIX"])
    require("OSS_KEY_PREFIX" not in business)
    return {
        "status": "passed_fresh_identity_and_privilege_probes",
        "database": receipt["database"],
        "roles": roles,
        "business_profile_has_no_owner_or_purpose_dsn": True,
        "separate_purpose_profiles_contain_only_one_dsn": True,
        "api_write_rollback_probe": True,
        "empty_business_tables_preserved": True,
        "worker_bid_update_denied": True,
        "worker_governance_export_select_denied": True,
        "purpose_raw_samples_and_exports_denied": True,
        "purpose_cross_read_function_denied": True,
        "purpose_own_read_function_execute_allowed": True,
        "score_read_update_denied": True,
        "native_from_env_prefix": native.prefix,
        "native_from_env_prefix_matches_new_profile": True,
        "oss_requests": 0,
        "model_calls": 0,
        "no_business_process_started": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--created-receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(probe(args.created_receipt), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"status": "failed", "code": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
