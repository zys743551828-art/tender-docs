"""First bounded technical-total declaration parser, never a general text inference.

Only a complete source evidence block whose whole statement explicitly declares the
technical maximum is supported. Selecting a number or a quote fragment cannot turn
an outcome/weight/negated/obsolete/ambiguous paragraph into a prebid denominator.
Unrecognised clauses stay pending for a later reviewed parser method.
"""

import re
from decimal import Decimal

from packages.domain.runtime import DomainError

# Full block rather than a client-selected substring; avoid the suffix/partial-token
# mistake previously found in pricing units. No blacklist of known bad prefixes.
DECLARATION = re.compile(
    r"\A\s*(?:技术总分满分|技术总满分|技术部分满分|技术评分满分|技术标满分|技术总分)"
    r"\s*(?:为|是|[:：])?\s*([0-9]+(?:\.[0-9]+)?)\s*分"
    r"(?:\s*[，,；;]\s*(?:量尺|计分口径)\s*(?:为|是|[:：])\s*([^。\n；;]+))?"
    r"\s*[。.]?\s*\Z"
)


def validate_maximum_declaration(*, source_text, quote, maximum, scale_description):
    """Return a bounded parsed denominator only after complete statement agreement.

    The caller must separately validate source kind, official tender membership,
    identity/lot/round, actual parse/bytes/quote and temporal/proof authority. This
    function alone never returns a qualified TechnicalScaleAuthority.
    """
    if (
        not isinstance(source_text, str)
        or not isinstance(quote, str)
        or source_text.strip() != quote.strip()
    ):
        raise DomainError(
            "TECHNICAL_SCALE_UNSUPPORTED", "请核对完整技术满分声明，不能只选无上下文数字", 409
        )
    match = DECLARATION.fullmatch(source_text)
    if match is None:
        raise DomainError(
            "TECHNICAL_SCALE_UNSUPPORTED", "当前声明句型不支持自动核验，请保存待核对", 409
        )
    parsed = Decimal(match.group(1))
    if (
        not parsed.is_finite()
        or parsed <= 0
        or parsed > Decimal("1000000000")
        or parsed != Decimal(maximum)
    ):
        raise DomainError("TECHNICAL_SCALE_MISMATCH", "所填满分与完整技术分声明不一致", 409)
    declared_scale = match.group(2)
    if declared_scale is None or declared_scale.strip() != scale_description.strip():
        raise DomainError(
            "TECHNICAL_SCALE_MISMATCH", "量尺须与同一完整声明中的明确计分口径一致", 409
        )
    return parsed
