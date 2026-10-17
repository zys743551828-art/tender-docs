"""Actual small synthetic PDF bytes, PDFium destinations/text; no DOCX or Docker."""

from copy import deepcopy
from uuid import UUID

import pytest

from packages.authoring.page_navigation import anchor_for, converge_navigation, inspect_navigation
from packages.domain.runtime import DomainError, sha

STYLE = {
    "margin_top_twips": 1440,
    "margin_bottom_twips": 1440,
    "margin_left_twips": 720,
    "margin_right_twips": 720,
}
IDS = [str(UUID(int=i)) for i in [1, 2, 3]]


def entries(pages=None):
    pages = pages or [None, None, None]
    return [
        {
            "node_id": identity,
            "parent_id": IDS[0] if index == 1 else None,
            "anchor": anchor_for(identity),
            "title": ["1 Alpha", "1.1 Child", "2 Omega"][index],
            "expected_start_page": pages[index],
        }
        for index, identity in enumerate(IDS)
    ]


def small_pdf(
    items=None,
    *,
    starts=None,
    count=6,
    named=True,
    action=None,
    wrong_title=False,
    missing=False,
    wrong_number=False,
    duplicate=False,
    target_top=770,
    extra_body_link=False,
    heading_graphics="",
):
    items = items or entries()
    starts = starts or [2, 3, 5]
    objects = [None, None]

    def add(value):
        objects.append(value.encode() if isinstance(value, str) else value)
        return len(objects)

    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = [add("") for _ in range(count)]

    def literal(text):
        return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"

    def dest(index):
        return f"[{page_ids[starts[index] - 1]} 0 R /XYZ 36 {target_top} 0]"

    for number in range(1, count + 1):
        commands = ["BT /F1 10 Tf 36 810 Td (Header repeated title) Tj ET"]
        annots = []
        if number == 1:
            for i, item in enumerate(items):
                y = 750 - i * 30
                commands.append(f"BT /F1 12 Tf 50 {y} Td {literal(item['title'])} Tj ET")
                printed = item["expected_start_page"]
                text = "—" if printed is None else str(printed + (1 if wrong_number else 0))
                if printed is not None:
                    commands.append(f"BT /F1 12 Tf 400 {y} Td {literal(text)} Tj ET")
                if missing and i == 1:
                    continue
                target = f"/Dest {dest(i)}"
                if action == "goto":
                    target = f"/A << /S /GoTo /D {dest(i)} >>"
                elif action == "uri":
                    target = "/A << /S /URI /URI (https://example.invalid/no-request) >>"
                annotation = add(
                    f"<< /Type /Annot /Subtype /Link /Rect [50 {y - 6} 330 {y + 12}] /Border [0 0 0] {target} >>"
                )
                annots.append(annotation)
                if duplicate and i == 0:
                    annots.append(annotation)
        if extra_body_link and number == 2:
            annots.append(
                add(f"<< /Type /Annot /Subtype /Link /Rect [50 700 330 718] /Dest {dest(0)} >>")
            )
        for i, start in enumerate(starts):
            if number == start:
                title = "Wrong heading" if wrong_title and i == 1 else items[i]["title"]
                commands.append(f"BT {heading_graphics} /F1 18 Tf 36 750 Td {literal(title)} Tj ET")
                commands.append("BT /F1 12 Tf 36 720 Td (Body with 120 days) Tj ET")
        stream = "\n".join(commands).encode()
        content = add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        objects[page_ids[number - 1] - 1] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R /Annots ["
            + " ".join(f"{a} 0 R" for a in annots)
            + "] >>"
        ).encode()
    names = ""
    if named:
        names = (
            " /Names << /Dests << /Names ["
            + " ".join(f"{literal(item['anchor'])} {dest(i)}" for i, item in enumerate(items))
            + "] >> >>"
        )
    objects[0] = ("<< /Type /Catalog /Pages 2 0 R" + names + " >>").encode()
    objects[1] = (
        "<< /Type /Pages /Count "
        + str(count)
        + " /Kids ["
        + " ".join(f"{p} 0 R" for p in page_ids)
        + "] >>"
    ).encode()
    data = bytearray(b"%PDF-1.7\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(data)


def test_actual_named_destinations_titles_toc_and_parent_ranges():
    items = entries([2, 3, 5])
    pdf = small_pdf(items)
    result = inspect_navigation(pdf, items, style=STYLE)
    assert result["coverage"] == "complete", result
    assert [(r["start_page"], r["end_page"]) for r in result["entries"]] == [(2, 4), (3, 4), (5, 6)]
    assert result["observed_toc_pages"] == [1]
    assert result["pdf_digest"] == sha(pdf)
    assert all(r["mapping_method"] == "named_destination_and_toc_link" for r in result["entries"])


def test_unique_title_mapping_without_named_dest_and_duplicate_rect_dedup():
    items = entries([2, 3, 5])
    result = inspect_navigation(small_pdf(items, named=False, duplicate=True), items, style=STYLE)
    assert result["coverage"] == "complete", result
    assert all(r["mapping_method"] == "unique_toc_link_title" for r in result["entries"])


@pytest.mark.parametrize(
    "option,gap",
    [
        ("wrong_title", "NAV_TARGET_PAGE_FIRST_HEADING_MISMATCH"),
        ("missing", "NAV_TOC_TARGET_MISSING_OR_AMBIGUOUS"),
        ("wrong_number", "NAV_TOC_PRINTED_VALUE_MISMATCH"),
        ("extra_body_link", "NAV_TOC_ENTRY_MULTIPAGE_UNSUPPORTED"),
    ],
)
def test_actual_defects_stay_partial(option, gap):
    items = entries([2, 3, 5])
    result = inspect_navigation(small_pdf(items, **{option: True}), items, style=STYLE)
    assert result["coverage"] == "partial" and gap in result["gaps"], result


@pytest.mark.parametrize(
    "action,gap",
    [("goto", "NAV_GOTO_ACTION_UNSUPPORTED"), ("uri", "NAV_NONLOCAL_OR_UNKNOWN_ACTION")],
)
def test_action_destinations_are_not_silently_enabled(action, gap):
    items = entries([2, 3, 5])
    result = inspect_navigation(small_pdf(items, action=action), items, style=STYLE)
    assert result["coverage"] == "partial" and gap in result["gaps"]


def test_destination_coordinate_and_continuous_end_are_honest():
    items = entries([2, 3, 5])
    result = inspect_navigation(small_pdf(items, target_top=400), items, style=STYLE)
    assert "NAV_DESTINATION_NOT_AT_HEADING" in result["gaps"]
    result = inspect_navigation(small_pdf(items), items, mode="continuous", style=STYLE)
    assert result["coverage"] == "partial" and "NAV_CONTINUOUS_END_PAGES_UNKNOWN" in result["gaps"]
    assert all(r["end_page"] is None for r in result["entries"])


def test_real_pdf_iterations_require_actual_printed_numbers_and_derive_fixed_point():
    first, second = entries(), entries([2, 3, 5])
    result = converge_navigation(
        [
            {
                "pdf_bytes": small_pdf(first),
                "docx_bytes": b"actual synthetic DOCX bytes iteration1",
                "entries": first,
            },
            {
                "pdf_bytes": small_pdf(second),
                "docx_bytes": b"actual synthetic DOCX bytes iteration2",
                "entries": second,
            },
        ],
        context_digest="a" * 64,
        style=STYLE,
    )
    assert result["fixed_point"] and result["coverage"] == "complete", result
    assert len(result["iterations"]) == 2 and result["iterations"][1]["docx_digest"] == sha(
        b"actual synthetic DOCX bytes iteration2"
    )
    bad = deepcopy(second)
    bad[0]["expected_start_page"] = 3
    result = converge_navigation(
        [
            {"pdf_bytes": small_pdf(first), "docx_bytes": b"x", "entries": first},
            {"pdf_bytes": small_pdf(bad), "docx_bytes": b"y", "entries": bad},
        ],
        context_digest="a" * 64,
        style=STYLE,
    )
    assert (
        not result["fixed_point"]
        and "NAV_TOC_ITERATION_NOT_DERIVED_FROM_PREVIOUS_MEASUREMENT" in result["gaps"]
    )


def test_three_round_budget_and_nonconvergence():
    items = entries()
    iteration = {"pdf_bytes": small_pdf(items), "docx_bytes": b"x", "entries": items}
    result = converge_navigation([iteration], context_digest="a" * 64, style=STYLE)
    assert not result["fixed_point"] and "NAV_TOC_NUMBERS_NOT_AT_FIXED_POINT" in result["gaps"]
    result = converge_navigation([iteration] * 4, context_digest="a" * 64, style=STYLE)
    assert not result["fixed_point"] and result["gaps"] == ["NAV_TOC_RENDER_LIMIT_EXCEEDED"]


def test_inputs_reject_noncanonical_identity_and_nonpreorder_tree():
    with pytest.raises(DomainError):
        anchor_for("not-a-node")
    bad = entries()
    bad[0]["parent_id"] = IDS[2]
    with pytest.raises(DomainError):
        inspect_navigation(small_pdf(), bad, style=STYLE)
    result = inspect_navigation(b"not a PDF", entries(), style=STYLE)
    assert result["coverage"] == "partial" and result["gaps"] == ["NAV_PDF_UNREADABLE"]


def test_three_actual_pagination_rounds_converge_after_page_count_changes():
    one, two, three = entries(), entries([2, 3, 5]), entries([3, 4, 6])
    rounds = [
        {"pdf_bytes": small_pdf(one), "docx_bytes": b"one", "entries": one},
        {
            "pdf_bytes": small_pdf(two, starts=[3, 4, 6], count=7),
            "docx_bytes": b"two",
            "entries": two,
        },
        {
            "pdf_bytes": small_pdf(three, starts=[3, 4, 6], count=7),
            "docx_bytes": b"three",
            "entries": three,
        },
    ]
    result = converge_navigation(rounds, context_digest="b" * 64, style=STYLE)
    assert result["fixed_point"] and len(result["iterations"]) == 3, result
    assert [(e["start_page"], e["end_page"]) for e in result["final_navigation"]["entries"]] == [
        (3, 5),
        (4, 5),
        (6, 7),
    ]
    rounds[-1]["pdf_bytes"] = small_pdf(three, starts=[4, 5, 7], count=8)
    failed = converge_navigation(rounds, context_digest="b" * 64, style=STYLE)
    assert not failed["fixed_point"] and "NAV_TOC_NUMBERS_NOT_AT_FIXED_POINT" in failed["gaps"]


def test_duplicate_titles_need_named_identity_and_changed_structure_breaks_proof():
    items = entries([2, 3, 5])
    for item in items:
        item["title"] = "Same heading"
    assert inspect_navigation(small_pdf(items), items, style=STYLE)["coverage"] == "complete"
    assert (
        inspect_navigation(small_pdf(items, named=False), items, style=STYLE)["coverage"]
        == "partial"
    )
    first = entries()
    second = entries([2, 3, 5])
    second[0]["title"] = "Changed snapshot heading"
    result = converge_navigation(
        [
            {"pdf_bytes": small_pdf(first), "docx_bytes": b"one", "entries": first},
            {"pdf_bytes": small_pdf(second), "docx_bytes": b"two", "entries": second},
        ],
        context_digest="b" * 64,
        style=STYLE,
    )
    assert (
        not result["fixed_point"] and "NAV_TOC_STRUCTURE_CHANGED_BETWEEN_RENDERS" in result["gaps"]
    )


@pytest.mark.parametrize("invisible", ["3 Tr", "1 1 1 rg"])
def test_invisible_target_heading_is_not_reliable_page_evidence(invisible):
    items = entries([2, 3, 5])
    source = small_pdf(items, heading_graphics=invisible)
    result = inspect_navigation(source, items, style=STYLE)
    assert result["page_count"] == 6
    assert result["coverage"] == "partial"
    assert "NAV_TARGET_PAGE_FIRST_HEADING_MISMATCH" in result["gaps"]
