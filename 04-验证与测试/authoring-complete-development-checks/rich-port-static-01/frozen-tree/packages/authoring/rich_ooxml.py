"""Native table OOXML; cell contents are emitted by the parent's strict writer."""

from xml.etree import ElementTree as ET  # nosec B405

from packages.authoring.build import W, element
from packages.authoring.rich_content import table_grid


def write_table(table, write_blocks, *, available_width_twips):
    """Emit all continuation cells explicitly, preserving rectangular span width."""
    checked = table_grid(table, available_width_twips=available_width_twips)
    grid, widths = checked["grid"], checked["column_widths_twips"]
    result = ET.Element("{" + W + "}tbl")
    properties = element(result, "tblPr")
    element(properties, "tblW", w=sum(widths), type="dxa")
    element(properties, "tblLayout", type="fixed")
    borders = element(properties, "tblBorders")
    for side in ["top", "left", "bottom", "right", "insideH", "insideV"]:
        element(borders, side, val="single", sz=4, color="000000")
    column_grid = element(result, "tblGrid")
    for width in widths:
        element(column_grid, "gridCol", w=width)
    for y, row in enumerate(grid):
        output_row = element(result, "tr")
        x = 0
        while x < len(widths):
            origin = row[x]
            attrs = origin["cell"]["attrs"]
            span = attrs["colspan"]
            output_cell = element(output_row, "tc")
            cell_properties = element(output_cell, "tcPr")
            element(cell_properties, "tcW", w=sum(widths[x : x + span]), type="dxa")
            if span > 1:
                element(cell_properties, "gridSpan", val=span)
            if attrs["rowspan"] > 1:
                element(
                    cell_properties, "vMerge", val="restart" if origin["row"] == y else "continue"
                )
            if attrs["header"]:
                element(cell_properties, "shd", val="clear", fill="E7E6E6")
            if origin["row"] == y:
                write_blocks(output_cell, origin["cell"]["content"])
            # Word requires a terminal paragraph even for continuation cells.
            if not len(output_cell) > 1 or output_cell[-1].tag != "{" + W + "}p":
                element(output_cell, "p")
            x += span
    return result
