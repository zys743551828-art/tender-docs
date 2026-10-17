"""当前关闭资格要求受检正文完整；不以其余来源partial一刀切或靠引文猜未知页无关。"""

import re

from packages.domain.engineering import number

PAGE_REFERENCE = re.compile(
    r"(?:physical\s*page|pages?|p\.?|物理页|第)\s*([0-9]+|[零〇一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟]+)\s*(?:页)?",
    re.IGNORECASE,
)
DEPENDENCY = re.compile(
    r"暂定|暂按|待.{0,20}确认|以.{0,60}为准|详见|参见|subject\s+to|refer\s+to|see\s+(?:the\s+)?(?:final|schedule|appendix)|final\s+(?:duration\s+)?schedule",
    re.IGNORECASE,
)


def evaluate(c, *, manifest, rule_id, evidence_ids, operation_id=None):
    body = manifest["body_version_id"]
    parses = manifest.get("parse_run_ids", [])
    body_parses = c.execute(
        "SELECT id,page_count,coverage,gaps FROM parse_runs WHERE document_version_id=%s AND id=ANY(%s::uuid[])",
        (body, parses),
    ).fetchall()
    if len(body_parses) != 1:
        return {
            "eligible": False,
            "required_pages": [],
            "unread_pages": [],
            "diagnostic_ids": [],
            "reason": "BODY_PARSE_SCOPE_UNPROVEN",
        }
    body_parse = body_parses[0]
    pages = c.execute(
        "SELECT p.physical_page,p.quality,p.gaps,coalesce(string_agg(e.text_content,E'\\n' ORDER BY e.ordinal),'') AS text_content FROM pages p JOIN parse_runs r ON r.id=p.parse_id AND r.bid_id=p.bid_id LEFT JOIN evidence_blocks e ON e.parse_id=p.parse_id AND e.physical_page=p.physical_page AND e.bid_id=p.bid_id WHERE r.document_version_id=%s AND r.id=ANY(%s::uuid[]) GROUP BY p.physical_page,p.quality,p.gaps ORDER BY p.physical_page",
        (body, parses),
    ).fetchall()
    supporting = c.execute(
        "SELECT e.id,e.physical_page,e.text_content FROM evidence_blocks e JOIN parse_runs r ON r.id=e.parse_id AND r.bid_id=e.bid_id WHERE r.document_version_id=%s AND r.id=ANY(%s::uuid[]) AND e.id=ANY(%s::uuid[]) ORDER BY e.physical_page,e.ordinal",
        (body, parses, list(evidence_ids)),
    ).fetchall()
    rule = c.execute(
        "SELECT conditions,exceptions FROM rule_items WHERE id=%s", (rule_id,)
    ).fetchone()
    # 尚无服务端可信“完整相关章节”生产器；未知正文页不得因没有交叉引用而视作无关。
    # 只要求这份受检正文，不把无关独立附件的partial等同于该问题复核失败。
    required = set(range(1, body_parse["page_count"] + 1))
    unresolved = False
    if not supporting:
        return {
            "eligible": False,
            "required_pages": [],
            "unread_pages": [],
            "diagnostic_ids": [],
            "reason": "SUPPORTING_BODY_EVIDENCE_MISSING",
        }
    # 对支持文字引用的页递归闭合；不把页号/正文自称替代真实读取。
    by_page = {row["physical_page"]: row for row in pages}
    pending, visited = list(required), set()
    while pending:
        page = pending.pop()
        if page in visited:
            continue
        visited.add(page)
        value = by_page.get(page)
        text = value["text_content"] if value else ""
        matches = list(PAGE_REFERENCE.finditer(text))
        linked = set()
        for match in matches:
            try:
                parsed = number(match.group(1))
                if parsed != parsed.to_integral_value() or parsed < 1:
                    unresolved = True
                    continue
                linked.add(int(parsed))
            except ValueError:
                unresolved = True
        required |= linked
        pending += list(linked - visited)
        if DEPENDENCY.search(text) and not matches:
            unresolved = True
    if rule and (rule["conditions"] or rule["exceptions"]):
        # 旧矩阵未冻结条件逐项范围证明，不能擅自断定条件/例外只涉及引文页。
        unresolved = True
    if unresolved:
        required |= set(by_page)
    unread = sorted(
        page
        for page in required
        if page not in by_page or by_page[page]["quality"] != "usable" or by_page[page]["gaps"]
    )
    diagnostic_ids = []
    core_affected = False
    if operation_id is not None:
        effects = c.execute(
            "SELECT result FROM effect_receipts WHERE operation_id=%s", (operation_id,)
        ).fetchall()
        core_affected = any(
            str(rule_id) in effect["result"].get("core_affected_rule_ids", []) for effect in effects
        )
        rows = c.execute(
            "SELECT id,code,field_path,evidence_ids FROM validation_diagnostics WHERE operation_id=%s",
            (operation_id,),
        ).fetchall()
        ids = {str(row["id"]) for row in supporting}
        diagnostic_ids = [
            str(row["id"])
            for row in rows
            if str(rule_id) in row["field_path"]
            or ids & set(map(str, row["evidence_ids"]))
            or row["code"] == "incomplete_range"
            and row["field_path"] == "read_evidence_ids"
        ]
    parse_complete = body_parse["coverage"] == "complete" and not body_parse["gaps"]
    reason = (
        "NECESSARY_RECHECK_RANGE_UNREAD"
        if unread
        else "BODY_RECHECK_SCOPE_UNPROVEN"
        if not parse_complete
        else "NECESSARY_RECHECK_DIAGNOSTIC_UNRESOLVED"
        if diagnostic_ids or core_affected
        else "NECESSARY_RECHECK_RANGE_READ"
    )
    return {
        "eligible": parse_complete and not unread and not diagnostic_ids and not core_affected,
        "required_pages": sorted(required),
        "unread_pages": unread,
        "diagnostic_ids": diagnostic_ids,
        "reason": reason,
    }
