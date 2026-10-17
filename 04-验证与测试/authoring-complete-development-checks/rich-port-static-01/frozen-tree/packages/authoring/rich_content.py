"""Closed RICH content validation, independent of the frozen text producer.

No image download or implicit table flattening is a product output. The temporary
text projection is used only to reuse the already strict text-node validator.
"""

from __future__ import annotations

import json
from uuid import UUID

from packages.authoring.content import SCHEMA as TEXT_SCHEMA
from packages.authoring.content import validate_text_content as validate_text
from packages.domain.runtime import DomainError, sha

SCHEMA = {
    "version": 2,
    "text_schema": TEXT_SCHEMA,
    "block_nodes": ["table", "image"],
    "table_nodes": ["tableRow", "tableCell"],
    "image_identity": ["asset_id", "asset_revision", "asset_version_id"],
    "limits": {"rows": 200, "columns": 100, "cells": 10000, "nodes": 10000},
}
SCHEMA_DIGEST = sha(SCHEMA)


def invalid(message="表格或图片结构不符合当前编制格式", code="INVALID_RICH_CONTENT"):
    raise DomainError(code, message, 422)


def text(value, maximum, *, required=False):
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (required and not value.strip())
        or any(ord(c) < 32 for c in value)
    ):
        invalid()


def positive(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        invalid()


def exact_node(value, kind, attrs):
    if (
        not isinstance(value, dict)
        or set(value) != {"type", "attrs", "content"}
        or value["type"] != kind
        or not isinstance(value["attrs"], dict)
        or set(value["attrs"]) != set(attrs)
        or not isinstance(value["content"], list)
    ):
        invalid()


def table_grid(table, *, available_width_twips=None):
    """Return complete cell occupancy, with only top-left origins owning content.

    Fully covered continuation rows may have no cells. Every other missing cell,
    overlap, crossing span, orphan column and dangling rowspan is rejected.
    """
    exact_node(table, "table", {"id", "column_widths_twips", "caption"})
    widths = table["attrs"]["column_widths_twips"]
    if not isinstance(widths, list) or not 1 <= len(widths) <= 100:
        invalid()
    for width in widths:
        positive(width, 20000)
    if available_width_twips is not None and sum(widths) > available_width_twips:
        invalid("表格总宽超过版心，请缩小列宽或调整版式边距", "AUTHORING_TABLE_TOO_WIDE")
    text(table["attrs"]["caption"], 1000)
    rows = table["content"]
    if not 1 <= len(rows) <= 200 or len(rows) * len(widths) > 10000:
        invalid("表格超过200行、100列或10000个网格的上限")
    grid: list[list[dict | None]] = [[None] * len(widths) for _ in rows]
    origins = []
    for y, row in enumerate(rows):
        exact_node(row, "tableRow", {"id"})
        x = 0
        for cell in row["content"]:
            exact_node(cell, "tableCell", {"id", "colspan", "rowspan", "header"})
            attrs = cell["attrs"]
            positive(attrs["colspan"], 100)
            positive(attrs["rowspan"], 200)
            if type(attrs["header"]) is not bool or not cell["content"]:
                invalid()
            if any(
                not isinstance(c, dict)
                or c.get("type") not in {"paragraph", "bulletList", "orderedList"}
                for c in cell["content"]
            ):
                invalid("单元格仅支持段落和列表，不支持嵌套表格或图片")
            while x < len(widths) and grid[y][x] is not None:
                x += 1
            right, bottom = x + attrs["colspan"], y + attrs["rowspan"]
            if right > len(widths) or bottom > len(rows):
                invalid("合并单元格超出表格边界")
            record = {"row": y, "column": x, "cell": cell}
            for yy in range(y, bottom):
                for xx in range(x, right):
                    if grid[yy][xx] is not None:
                        invalid("合并单元格与已有单元格重叠")
                    grid[yy][xx] = record
            origins.append(record)
            x = right
        if any(item is None for item in grid[y]):
            invalid("表格存在未填满网格，请补全单元格或修正合并范围")
    return {"grid": grid, "origins": origins, "column_widths_twips": widths}


def image(value, *, available_width_twips=None, available_height_twips=None):
    if not isinstance(value, dict) or set(value) != {"type", "attrs"} or value["type"] != "image":
        invalid()
    attrs = value["attrs"]
    if not isinstance(attrs, dict) or set(attrs) != {
        "id",
        "asset_id",
        "asset_revision",
        "asset_version_id",
        "width_twips",
        "height_twips",
        "alt",
        "caption",
    }:
        invalid()
    try:
        UUID(attrs["asset_id"])
        UUID(attrs["asset_version_id"])
    except (ValueError, TypeError, AttributeError):
        invalid()
    positive(attrs["asset_revision"], 2**63 - 1)
    positive(attrs["width_twips"], 20000)
    positive(attrs["height_twips"], 20000)
    text(attrs["alt"], 1000, required=True)
    text(attrs["caption"], 1000)
    if available_width_twips is not None and attrs["width_twips"] > available_width_twips:
        invalid("图片宽度超过版心，请缩小图片或调整版式边距", "AUTHORING_IMAGE_TOO_WIDE")
    if available_height_twips is not None and attrs["height_twips"] > available_height_twips:
        invalid("图片高度超过单页版心，请缩小图片或拆为多张图", "AUTHORING_IMAGE_TOO_TALL")


def validate_content(content, *, available_width_twips=None, available_height_twips=None):
    try:
        if len(json.dumps(content, ensure_ascii=False).encode()) > 1_000_000:
            invalid("章节超过1MiB，请拆分章节")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        invalid()
    if not isinstance(content, dict) or not isinstance(content.get("content"), list):
        invalid()
    seen, count = set(), 0

    def stable(value, depth=0):
        nonlocal count
        count += 1
        if count > 10000 or depth > 12 or not isinstance(value, dict):
            invalid()
        if value.get("type") not in {"doc", "text", "hardBreak"}:
            key = value.get("attrs", {}).get("id") if isinstance(value.get("attrs"), dict) else None
            if not isinstance(key, str) or not 1 <= len(key) <= 128 or key in seen:
                invalid("表图及正文节点ID必须唯一且稳定")
            seen.add(key)
        children = value.get("content", [])
        if not isinstance(children, list):
            invalid()
        for child in children:
            stable(child, depth + 1)

    # Match the legacy convention: top-level blocks start at depth zero.
    stable(content, -1)
    projected = []
    for block in content["content"]:
        if block.get("type") == "table":
            result = table_grid(block, available_width_twips=available_width_twips)
            for origin in result["origins"]:
                projected.extend(origin["cell"]["content"])
        elif block.get("type") == "image":
            image(
                block,
                available_width_twips=available_width_twips,
                available_height_twips=available_height_twips,
            )
        else:
            projected.append(block)
    validate_text(dict(content, content=projected))
    return content
