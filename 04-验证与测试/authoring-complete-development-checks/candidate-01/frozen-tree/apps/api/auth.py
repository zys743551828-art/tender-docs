"""登录会话和人工命令来源校验。"""

import hashlib
import hmac
import secrets

from fastapi import Request

from packages.domain.runtime import DomainError, connection, required, secret_hash

COOKIE = "session"


def csrf(token):
    return secret_hash(token + ":csrf", "CSRF_SECRET")


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return (
        f"pbkdf2_sha256$600000${salt}$"
        + hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
    )


def password_ok(password, encoded):
    try:
        scheme, iterations, salt, _ = encoded.split("$")
        return (
            scheme == "pbkdf2_sha256"
            and iterations == "600000"
            and hmac.compare_digest(password_hash(password, salt), encoded)
        )
    except (ValueError, TypeError):
        return False


def origin(request):
    if request.headers.get("origin") != required("APP_ORIGIN"):
        raise DomainError("ORIGIN_REJECTED", "请求来源不被允许", 403)


def auth(request: Request, write=False):
    token = request.cookies.get(COOKIE)
    if not token:
        raise DomainError("UNAUTHENTICATED", "请登录", 401)
    with connection() as c:
        row = c.execute(
            "SELECT s.*,m.display_name,m.active FROM sessions s JOIN members m ON m.id=s.member_id WHERE token_digest=%s AND revoked_at IS NULL AND expires_at>now() AND m.active",
            (secret_hash(token),),
        ).fetchone()
    if not row:
        raise DomainError("SESSION_EXPIRED", "登录已过期，请重新登录", 401)
    if write:
        origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(
            hashlib.sha256(supplied.encode()).hexdigest(), row["csrf_digest"]
        ):
            raise DomainError("CSRF_REJECTED", "请刷新登录状态后重试", 403)
    return row


def auth_view(row, token):
    return {
        "member": {
            "id": str(row["member_id"]),
            "display_name": row["display_name"],
            "active": row["active"],
        },
        "csrf_token": csrf(token),
        "expires_at": row["expires_at"].isoformat(),
    }
