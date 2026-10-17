"""Dedicated nonowner score RPC adapter. No general API/worker DSN or bucket access."""

import os
import threading
from contextlib import contextmanager
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError
from packages.model_governance.scoring_producer import execute


@contextmanager
def score_connection():
    # Deliberately no runtime.connection or .env fallback. This process must be launched
    # with a purpose-only environment; an API/owner/other CPU DSN cannot be substituted.
    allowed = {"MODEL_SCORE_DATABASE_DSN", "WORKBENCH_ENV_FILE", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    if set(os.environ) - allowed or os.environ.get("WORKBENCH_ENV_FILE") != "/dev/null":
        raise DomainError(
            "SCORE_ENVIRONMENT_NOT_ISOLATED",
            "专用评分进程必须以单一用途白名单环境启动，不能继承通用env/桶或标签权限",
            503,
        )
    dsn = os.environ.get("MODEL_SCORE_DATABASE_DSN")
    if not dsn:
        raise DomainError("SCORE_RUNTIME_NOT_CONFIGURED", "专用评分用途连接尚未配置", 503)
    with psycopg.connect(
        dsn,
        row_factory=dict_row,
        options="-c search_path=score_execution,pg_catalog -c statement_timeout=15000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=15000",
        application_name="tender-score-purpose",
    ) as c:
        role = c.execute(
            "SELECT r.rolsuper,r.rolbypassrls,has_schema_privilege(current_user,'design','USAGE') AS raw_access,pg_has_role(current_user,'tender_model_score','MEMBER') AS scorer FROM pg_roles r WHERE r.rolname=current_user"
        ).fetchone()
        if (
            role is None
            or role["rolsuper"]
            or role["rolbypassrls"]
            or role["raw_access"]
            or not role["scorer"]
        ):
            raise DomainError(
                "SCORE_ROLE_INVALID",
                "评分进程须独立受限用途登录，不能使用API、owner或普通Worker权限",
                503,
            )
        yield c


class ScoreAuthority:
    """Every RPC closes before returning to model compute/HTTP. No caller source list."""

    @staticmethod
    def _call(query, values):
        try:
            with score_connection() as c:
                return c.execute(query, values).fetchone()["value"]
        except psycopg.Error as error:
            if error.sqlstate in {"42883", "3F000"}:
                raise DomainError(
                    "SCORE_AUTHORITY_NOT_CONFIGURED", "评分权威尚未按独立基线初始化", 503
                ) from None
            if error.sqlstate == "40001":
                raise DomainError(
                    "SCORE_GENERATION_CHANGED", "评分代次或有效上下文已变化", 409
                ) from None
            if error.sqlstate in {"55000", "22023", "23514", "23505", "P0002"}:
                raise DomainError(
                    "SCORE_AUTHORITY_UNAVAILABLE",
                    "评分范围、租约、账本或预算不满足冻结权威，未自动重试",
                    409,
                ) from None
            raise

    @staticmethod
    def _scope(lease):
        return lease["job_id"], lease["generation"], lease["fence"], lease["owner"]

    def claim(self, identity, generation):
        return self._call(
            "SELECT score_execution.claim_job(%s,%s,%s) AS value", (identity, generation, uuid4())
        )

    def input(self, lease):
        return self._call(
            "SELECT score_execution.owned_input(%s,%s,%s,%s) AS value", self._scope(lease)
        )

    def heartbeat(self, lease):
        self._call("SELECT score_execution.heartbeat(%s,%s,%s,%s) AS value", self._scope(lease))

    def reserve(
        self, lease, *, chapter_id, replicate, request_digest, profile_digest, reserved_fee
    ):
        return self._call(
            "SELECT score_execution.reserve_call(%s,%s,%s,%s,%s,%s,%s,%s,%s) AS value",
            (
                *self._scope(lease),
                chapter_id,
                replicate,
                request_digest,
                profile_digest,
                reserved_fee,
            ),
        )

    def success(self, lease, identity):
        value = self._call(
            "SELECT score_execution.success_response(%s,%s,%s,%s,%s) AS value",
            (*self._scope(lease), identity),
        )
        if value is None:
            raise DomainError(
                "SCORE_RECONCILIATION_REQUIRED", "原调用未有可靠成功结果，禁止补发", 409
            )
        return value

    def settle(self, identity, lease, *, state, response, estimated_fee):
        self._call(
            "SELECT score_execution.settle_call(%s,%s,%s,%s,%s) AS value",
            (identity, lease["owner"], state, response, estimated_fee),
        )

    def pause_failed(self, lease):
        self._call("SELECT score_execution.pause_failed(%s,%s,%s,%s) AS value", self._scope(lease))

    def publish(self, lease, document):
        return self._call(
            "SELECT score_execution.publish_result(%s,%s,%s,%s,%s) AS value",
            (*self._scope(lease), Jsonb(document)),
        )


def execute_job(identity: UUID, generation: int, *, transport=None):
    if transport is None:
        # No implicit paid construction in the current release. A reviewed future runtime
        # may inject DeepSeekClient after its exact execution scope is authorized.
        raise DomainError(
            "SCORE_PAID_EXECUTION_DISABLED",
            "当前评分实际模型执行未开放；软件机制使用显式测试端口",
            503,
        )
    authority = ScoreAuthority()
    lease = authority.claim(identity, generation)
    stop = threading.Event()
    failures: list[bool] = []

    def beat():
        while not stop.wait(20):
            try:
                authority.heartbeat(lease)
            except Exception:
                failures.append(True)
                return

    thread = threading.Thread(target=beat, name="score-purpose-heartbeat", daemon=True)
    thread.start()
    try:
        result = execute(authority, lease, transport=transport)
        if failures:
            # Final publish already independently checked the live fence. This flag does
            # not authorize any resend or overwrite a committed receipt.
            raise DomainError(
                "SCORE_HEARTBEAT_INTERRUPTED", "租约维护出现中断，请读取已持久化任务状态核对", 409
            )
        return result
    except Exception:
        try:
            authority.pause_failed(lease)
        except Exception:
            # Accounting/source/fence may already have paused or cancelled this job.
            # Never alter a newer owner or hide the original execution failure.
            failures.append(True)
        raise
    finally:
        stop.set()
        thread.join(timeout=1)
