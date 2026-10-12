"""One export layout for every report: CSV for spreadsheets, PDF for print.

The PDF carries the RentWheels header, the date range, and a generated-by
line on every report, so a printout always answers what, when, and by whom.
Files land next to the receipts (same open-after-write habit) and callers
pass plain strings -- formatting stays in the report builders.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

EXPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "receipts"

_TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=16)
_HEAD = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=11)
_LINE = ParagraphStyle("line", fontName="Helvetica", fontSize=10)
_SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=9, textColor=colors.grey)

_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFE7D6")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9BCA6")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_") or "report"


def export_path(name: str, extension: str) -> Path:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return EXPORTS_DIR / f"report_{_slug(name)}_{stamp}.{extension}"


def write_csv(path: Path, headers: list[str], rows: list[list]) -> Path:
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows([[str(cell) for cell in row] for row in rows])
    return path


def write_pdf(
    path: Path,
    *,
    title: str,
    period: str,
    generated_by: str,
    summary: list[str],
    headers: list[str],
    rows: list[list],
) -> Path:
    story = [
        Paragraph("RentWheels", _TITLE),
        Paragraph(title, _HEAD),
        Paragraph(period, _SMALL),
        Paragraph(generated_by, _SMALL),
        Spacer(1, 0.15 * inch),
    ]
    for line in summary:
        story.append(Paragraph(line, _LINE))
    story.append(Spacer(1, 0.15 * inch))
    data = [headers, *[[str(cell) for cell in row] for row in rows]]
    story.append(Table(data, repeatRows=1, style=_TABLE_STYLE))
    SimpleDocTemplate(
        str(path), pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
    ).build(story)
    return path
