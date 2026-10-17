"""人工更正不悄悄启动或继续扩大尚未冻结的评审请求。"""

from psycopg.types.json import Jsonb

from packages.domain.runtime import clean, now


def invalidate_pending(c, bid, reason, code="REVIEW_INPUT_CHANGED"):
    row = c.execute(
        "SELECT pending_intent FROM review_slots WHERE bid_id=%s FOR UPDATE", (bid,)
    ).fetchone()
    intent = row["pending_intent"] if row else None
    if not intent or intent.get("review_id") or intent["state"] != "preparing":
        return
    intent.update(state="blocked", error_code=code, gaps=[reason], updated_at=now().isoformat())
    c.execute(
        "UPDATE review_slots SET pending_intent=%s,revision=revision+1 WHERE bid_id=%s",
        (Jsonb(clean(intent)), bid),
    )
