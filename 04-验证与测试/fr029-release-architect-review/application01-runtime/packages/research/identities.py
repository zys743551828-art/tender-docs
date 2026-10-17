"""实交身份只用显式冻结证据；历史审计观察不能单独授正式样本资格。"""

from packages.domain.runtime import DomainError
from packages.identity import repository as identities


def submission_identity(c, submission):
    frozen = (submission.get("evidence") or {}).get("identity")
    unavailable = {
        "status": "unavailable",
        "reason": "SUBMISSION_IDENTITY_UNAVAILABLE",
        "snapshot": None,
        "source": "submission_evidence",
    }
    if (
        not isinstance(frozen, dict)
        or frozen.get("binding_status") != "confirmed"
        or not frozen.get("authority_id")
    ):
        return unavailable
    proof = c.execute(
        "SELECT payload FROM audit_entries WHERE id=%s AND bid_id=%s AND event_type=%s",
        (frozen["authority_id"], submission["bid_id"], identities.BOUND),
    ).fetchone()
    if (
        proof is None
        or proof["payload"].get("confirmed") is not True
        or any(frozen.get(key) != value for key, value in proof["payload"]["after"].items())
    ):
        return unavailable | {"reason": "SUBMISSION_IDENTITY_AUTHORITY_INVALID"}
    current = identities.snapshot(c, submission["bid_id"])
    if current != frozen:
        return {
            "status": "changed",
            "reason": "SUBMISSION_IDENTITY_CHANGED",
            "snapshot": frozen,
            "source": "submission_evidence",
        }
    try:
        identities.require_bidder(c, frozen["bidder_id"])
    except DomainError:
        return unavailable | {"reason": "SUBMISSION_BIDDER_UNREGISTERED"}
    return {
        "status": "confirmed",
        "reason": None,
        "snapshot": frozen,
        "source": "submission_evidence",
    }


def require_submission_identity(c, submission):
    result = submission_identity(c, submission)
    if result["status"] != "confirmed":
        raise DomainError(
            "OUTCOME_IDENTITY_UNVERIFIED",
            "实际提交的主体、工程轮次没有明确冻结依据，不能用当前身份或历史观察补造",
            409,
            {"reason": result["reason"]},
        )
    return result
