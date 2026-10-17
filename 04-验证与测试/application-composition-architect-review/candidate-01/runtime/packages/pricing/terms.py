"""保守的完整金额声明语法；不能从未知货币词尾猜测受支持单位。"""

import re
from typing import NoReturn

from packages.domain.runtime import DomainError

# Values terminate only at explicit clause punctuation, not at a legal suffix or whitespace.
DECLARATION = re.compile(
    r"(?:金额|报价|投标金额|投标报价)单位\s*[：:=]?\s*(?:为\s*)?([^，,；;。！？!?]+)"
    r"|以\s*([^，,；;。！？!?]+?)\s*为单位"
)
CURRENCY = re.compile(r"(?:币种|货币种类)\s*[：:=]?\s*(?:为\s*)?([^，,；;。！？!?]+)")


def reject(field="amount_unit") -> NoReturn:
    raise DomainError(
        "PRICING_UNIT_WITHOUT_EVIDENCE",
        "须有同页完整、明确且一致的金额单位与币种声明；未知词、混用及跨页拼接不支持",
        422,
        {"field": field},
    )


def declarations(text):
    units = []
    currencies = []
    for match in DECLARATION.finditer(text):
        token = re.sub(r"\s+", "", match.group(1) or match.group(2))
        supported = re.fullmatch(r"(人民币)?(万元|元)", token)
        if supported is None:
            reject()
        units.append(supported.group(2))
        if supported.group(1):
            currencies.append("CNY")
    for match in CURRENCY.finditer(text):
        token = re.sub(r"\s+", "", match.group(1))
        if token not in {"CNY", "人民币"}:
            reject("currency")
        currencies.append("CNY")
    # A stated numeric amount is also checked as a whole token, never a currency suffix.
    for match in re.finditer(r"[0-9]+(?:\.[0-9]+)?\s*([^0-9，,；;。！？!?\s]*元)", text):
        token = match.group(1)
        supported = re.fullmatch(r"(人民币)?(万元|元)", token)
        if supported is None:
            reject()
        units.append(supported.group(2))
        if supported.group(1):
            currencies.append("CNY")
    return units, currencies


def page_key(quote):
    return tuple(str(quote[key]) for key in ("document_version_id", "parse_id", "physical_page"))


def rounding_policy_text(text):
    """Exclude an explicit, terminated no-rounding clause from positive rounding detection.

    Ambiguous continuations remain visible to the conservative refusal rule. A later
    affirmative baseline-rounding clause is never removed by this normalization.
    """
    return re.sub(
        r"基准价\s*不(?:予|作|做|进行)?\s*(?:任何)?\s*(?:中间)?(?:四舍五入|取整)"
        r"(?=\s*(?:[。；;\r\n]|$))",
        "明确无需基准值舍入",
        text,
    )


def validate_quotes(value):
    quotes = value["evidence_quotes"]
    method_pages = {page_key(q) for q in quotes if "method" in q["supported_fields"]}
    if len(method_pages) != 1:
        reject()
    anchor = next(iter(method_pages))
    for field in ("amount_unit", "currency"):
        supporting = [q for q in quotes if field in q["supported_fields"]]
        for quote in supporting:
            if page_key(quote) != anchor:
                reject(field)
            units, currencies = declarations(quote["quote"])
            # A selected single-character block cannot borrow an unrelated declaration.
            if field == "amount_unit" and (
                not DECLARATION.search(quote["quote"]) or set(units) != {value[field]}
            ):
                reject(field)
            if field == "currency" and set(currencies) != (
                {value[field]} if value[field] is not None else set()
            ):
                reject(field)
    units, currencies = declarations("\n".join(q["quote"] for q in quotes))
    if set(units) != {value["amount_unit"]}:
        reject()
    if set(currencies) != ({value["currency"]} if value["currency"] is not None else set()):
        reject("currency")
    return anchor


def validate_page(value, text):
    units, currencies = declarations(text)
    if set(units) != {value["amount_unit"]}:
        reject()
    if set(currencies) != ({value["currency"]} if value["currency"] is not None else set()):
        reject("currency")
