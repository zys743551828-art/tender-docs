"""仅get_bucket_cors读配置，评估指定浏览器Origin，不写bucket或对象。"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path

import oss2

from qa.provider_preflight import configuration, safe_error, write

ORIGIN = "http://127.0.0.1:5175"


def main():
    result = dict(
        scope="指定OSS bucket CORS只读配置检查；不是实际浏览器签名上传/下载验收",
        origin=ORIGIN,
        read_completed=False,
        started_at=datetime.now(timezone.utc).isoformat(),
        object_list_requests=0,
        object_mutations=0,
        bucket_configuration_mutations=0,
        chat_requests=0,
        checks=[],
    )
    try:
        values = configuration()
        bucket = oss2.Bucket(
            oss2.AuthV4(values["OBJECT_STORE_ACCESS_KEY"], values["OBJECT_STORE_SECRET_KEY"]),
            values["OBJECT_STORE_ENDPOINT"],
            values["OBJECT_STORE_BUCKET"],
            region=values["OBJECT_STORE_REGION"],
            connect_timeout=20,
        )
        try:
            cors = bucket.get_bucket_cors()
            rules = cors.rules
            result["response"] = dict(status=cors.status, request_id=cors.request_id)
        except oss2.exceptions.ServerError as error:
            if error.status != 404 or error.code != "NoSuchCORSConfiguration":
                raise
            rules = []
            result["response"] = dict(
                status=error.status, code=error.code, request_id=error.request_id
            )
        result["read_completed"] = True
        summarized = []
        for rule in rules:
            matched = [pattern for pattern in rule.allowed_origins if fnmatchcase(ORIGIN, pattern)]
            summarized.append(
                dict(
                    origin_count=len(rule.allowed_origins),
                    target_origin_match=bool(matched),
                    matching_origin_patterns=matched,
                    allowed_methods=rule.allowed_methods,
                    allowed_headers=rule.allowed_headers,
                    expose_headers=rule.expose_headers,
                    max_age_seconds=rule.max_age_seconds,
                )
            )
        result["rules"] = summarized
        result["rule_count"] = len(rules)
        evaluations = {}
        for method, required in [("PUT", ["content-type"]), ("GET", []), ("HEAD", [])]:
            matches = []
            for index, rule in enumerate(summarized):
                if not rule["target_origin_match"] or method not in rule["allowed_methods"]:
                    continue
                if all(
                    any(fnmatchcase(header, pattern.lower()) for pattern in rule["allowed_headers"])
                    for header in required
                ):
                    matches.append(index)
            evaluations[method] = dict(
                allowed_by_declared_rules=bool(matches),
                matching_rule_indexes=matches,
                required_request_headers=required,
            )
        result["methods"] = evaluations
        result["configured_for_requested_origin"] = all(
            row["allowed_by_declared_rules"] for row in evaluations.values()
        )
        result["credentials_mode"] = "跨域存储请求不发送Cookie；签名在URL参数，不以CORS代替对象授权"
        result["actual_signed_object_browser_requests_executed"] = False
        result["checks"].append(
            "仅读取指定bucket CORS，按实际前端PUT Content-Type及GET/HEAD检查规则"
        )
        result["conclusion"] = (
            "configured_by_rules_pending_browser"
            if result["configured_for_requested_origin"]
            else "cors_missing_or_not_matching_pm_resolution_needed"
        )
    except Exception as error:
        result["failure"] = safe_error(error)
        for field in ["status", "code", "request_id"]:
            if hasattr(error, field):
                result["failure"][field] = getattr(error, field)
        result["conclusion"] = "read_blocked"
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    write(Path(os.environ["QA_PROVIDER_OUTPUT"]), result)
    print(
        dict(
            read_completed=result["read_completed"],
            configured=result.get("configured_for_requested_origin"),
            rule_count=result.get("rule_count"),
            conclusion=result["conclusion"],
        )
    )
    return 0 if result["read_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
