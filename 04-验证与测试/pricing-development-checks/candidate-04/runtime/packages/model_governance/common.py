"""Pure helpers: deliberately no domain.runtime import or automatic .env loading."""

import hashlib
import json
import math
from datetime import datetime
from decimal import Decimal, InvalidOperation


class GovernanceError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def digest(value):
    raw = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def finite(value, *, lower=None, upper=None):
    if isinstance(value, bool):
        raise GovernanceError("INVALID_NUMBER", "Boolean is not a numeric observation")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise GovernanceError("INVALID_NUMBER", "Finite bounded number required") from None
    if (
        not math.isfinite(result)
        or (lower is not None and result < lower)
        or (upper is not None and result > upper)
    ):
        raise GovernanceError("INVALID_NUMBER", "Number outside declared bounds")
    return result


def decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise GovernanceError("INVALID_NUMBER", "Finite decimal required") from None
    if not result.is_finite() or result < 0 or result > Decimal("1000000000"):
        raise GovernanceError("INVALID_NUMBER", "Nonnegative bounded decimal required")
    return result


def instant(value):
    try:
        result = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
        if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise GovernanceError("TIME_UNKNOWN", "Typed timezone-aware time required") from None
