"""独立连接实际运行DSN验证LOGIN能力，口令和DSN不输出。"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from qa.helpers import ensure, write_result


def main():
    result = dict(
        scope="实际QA配置的非owner LOGIN与禁止权限，不使用初始化owner",
        passed=False,
        checks=[],
        identities=[],
        executed_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        for role in ["API", "WORKER", "RESEARCH_WORKER", "SCORE_READ"]:
            with psycopg.connect(os.environ[role + "_DATABASE_DSN"], row_factory=dict_row) as c:
                row = c.execute(
                    "SELECT current_user,current_database(),rolsuper,rolcanlogin,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
                ).fetchone()
                owns = c.execute(
                    "SELECT EXISTS(SELECT FROM pg_class WHERE relnamespace='design'::regnamespace AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) AS owns"
                ).fetchone()["owns"]
                ensure(
                    row["rolcanlogin"]
                    and not row["rolsuper"]
                    and not owns
                    and not row["rolcreatedb"]
                    and not row["rolcreaterole"],
                    role + "必须为限定非owner LOGIN",
                )
                result["identities"].append(
                    dict(
                        capability=role,
                        login=row["current_user"],
                        database=row["current_database"],
                        superuser=row["rolsuper"],
                        owns_tables=owns,
                    )
                )
                result["checks"].append(role + "实际LOGIN无owner/超级/建库/建角色能力")
        for role, statement in [
            ("SCORE_READ", "SELECT * FROM design.outcomes"),
            ("SCORE_READ", "SELECT * FROM design.messages"),
            ("WORKER", "UPDATE design.decisions SET decision=decision"),
            ("WORKER", "UPDATE design.model_routes SET revision=revision+1"),
            ("WORKER", "SELECT * FROM design.outcomes"),
            ("RESEARCH_WORKER", "SELECT * FROM design.outcomes"),
            ("RESEARCH_WORKER", "UPDATE design.model_routes SET revision=revision+1"),
        ]:
            with psycopg.connect(os.environ[role + "_DATABASE_DSN"]) as c:
                try:
                    c.execute(statement)
                except psycopg.Error as error:
                    ensure(error.sqlstate == "42501", role + "应以权限拒绝，非其他错误")
                else:
                    raise AssertionError(role + "禁止路径竟被允许")
            result["checks"].append(role + "禁止 " + statement)
        result["passed"] = True
    except Exception as e:
        result["failure"] = dict(
            type=type(e).__name__,
            message=str(e) if isinstance(e, AssertionError) else "连接/权限执行异常，不输出DSN",
        )
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_result(os.environ.get("QA_RESULT_PATH", "qa/runtime-roles-result.json"), result)
    print(dict(passed=result["passed"], checks=len(result["checks"])))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
