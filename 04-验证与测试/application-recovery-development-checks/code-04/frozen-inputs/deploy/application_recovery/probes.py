"""Actual restricted-login permission probes; no owner used to fix business failures."""

from __future__ import annotations

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from deploy.application_recovery import archive
from deploy.application_recovery import security as s


def denied(dsn, statement, params=None):
    with psycopg.connect(dsn) as c:
        try:
            c.execute(statement, params)
        except psycopg.errors.InsufficientPrivilege:
            c.rollback()
            return
        c.rollback()
    raise s.RecoveryError("EXPECTED_PRIVILEGE_DENIAL_WAS_NOT_ENFORCED")


def verify(cluster):
    reader = make_conninfo(
        s.connection_dsn(cluster, "reader"), options="-c default_transaction_read_only=off"
    )
    with psycopg.connect(reader) as c:
        archive.reader_guard(c, cluster)
        c.execute("SELECT * FROM model_release.certifications")
    for statement in (
        "INSERT INTO recovery_control.identity VALUES('forbidden')",
        "UPDATE recovery_control.identity SET value=value",
        "DELETE FROM recovery_control.identity",
        "TRUNCATE recovery_control.identity",
        "SELECT nextval('recovery_control.permission_probe')",
        "SELECT setval('recovery_control.permission_probe',1)",
        "CREATE TABLE design.must_not_exist(id integer)",
        "CREATE ROLE must_not_exist_recovery_probe",
        "SELECT model_governance.claim_job(NULL,NULL)",
        "SELECT model_governance.read_final(NULL,NULL,NULL)",
        "SELECT historical_input.seal_input(NULL,NULL)",
    ):
        denied(reader, statement)
    for group in (cluster["owner_label"], *s.GROUPS):
        denied(reader, sql.SQL("SET ROLE {}").format(s.identifier(group)))
    api = s.connection_dsn(cluster, "api")
    with psycopg.connect(api) as c:
        s.verify(c, cluster, "api", restricted=True)
    denied(api, "SELECT * FROM model_release.certifications")
    denied(api, "CREATE TABLE design.must_not_exist(id integer)")
    denied(api, sql.SQL("SET ROLE {}").format(s.identifier(cluster["owner_label"])))
    with psycopg.connect(api) as c:
        if c.execute(
            "SELECT has_database_privilege(%s,current_database(),'CONNECT')",
            (cluster["accounts"]["worker"]["user"],),
        ).fetchone() != (False,):
            raise s.RecoveryError("WORKER_HAS_CONNECT_PRIVILEGE")
    try:
        with psycopg.connect(s.connection_dsn(cluster, "worker")):
            pass
    except psycopg.OperationalError as error:
        if error.sqlstate not in (None, "42501"):
            raise s.RecoveryError("WORKER_CONNECT_DENIAL_NOT_CONFIRMED") from None
    else:
        raise s.RecoveryError("WORKER_CONNECTED_WHILE_WORK_PAUSED")
    return {
        "backup_all_table_read_verified": True,
        "backup_dml_ddl_rpc_role_and_sequence_denials": True,
        "api_certifications_still_42501": True,
        "api_nonowner": True,
        "worker_connect_rejected": True,
    }
