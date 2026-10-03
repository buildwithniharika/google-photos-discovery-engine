"""PM-ready PDF in the problem statement's Example Output shape. Built in memory."""

from __future__ import annotations

import xml.sax.saxutils as xml
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from discovery.export.bundle import ReportBundle
from discovery.export.sanitize import winansi_safe
from discovery.scoring.dimensions import DIMENSIONS

TOP_AREAS = 8
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)
_registered: str | None = None
_active_font = "Helvetica"

METHODOLOGY = (
    "Sources are public Google Photos feedback: Play Store reviews, the App Store reviews page, "
    "a Reddit and community Google Sheet, and Help Community threads. Text is cleaned and "
    "PII-redacted before analysis. A three-stage funnel keeps vaguely remembered photo retrieval "
    "and leaves storage, pricing, backup, sharing, and deletion out unless they block retrieval. "
    "An LLM extracts what the user remembered, forgot, tried, and where retrieval broke, and a "
    "quote is kept only when it matches the redacted text. Similar problems are clustered and "
    "scored on frequency, severity, strategic fit, evidence quality, product leverage, and "
    "research value. The weights in this report are the ones used for the ranking."
)

LIMITATIONS = (
    "This is a research readout, not a design for Google Photos search. The corpus is public "
    "posts, not a sample of all users, and the AI stages cover English text. Quotes are spans of "
    "redacted text and are shown without author names. This PDF omits high-stakes items "
    "(medical, financial, legal, or irreplaceable) and grief, and it replaces some personal "
    "names with [name]. A small weight change can move second and third place; the sensitivity "
    "check for this run is in eval/opportunity_scores.md. Clustering can join problems a reader "
    "would keep separate, and PM curation can rename, merge, or archive an area after this export."
)


def build_pdf(bundle: ReportBundle, *, top_n: int = TOP_AREAS, font: str | None = None) -> bytes:
    global _active_font
    font = font or _font_name()
    _active_font = font
    styles = _styles(font)
    story: list = []
    story.append(Paragraph("Vaguely remembered photo retrieval", styles["title"]))
    story.append(Paragraph("Opportunity report", styles["h1"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(_esc(_meta(bundle)), styles["meta"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Executive summary", styles["h1"]))
    story.append(Paragraph(_esc(_summary(bundle, top_n)), styles["body"]))
    if bundle.note:
        story.append(Spacer(1, 6))
        story.append(Paragraph(_esc(bundle.note), styles["note"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Ranked opportunity areas", styles["h1"]))
    story.append(_ranked_table(bundle, font))
    shown = bundle.areas[:top_n]
    for area in shown:
        story.append(PageBreak())
        story.extend(_area_page(area, styles))
    story.append(PageBreak())
    story.append(Paragraph("Methodology", styles["h1"]))
    story.append(Paragraph(_esc(METHODOLOGY), styles["body"]))
    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            _esc(
                "Weights: "
                + ", ".join(f"{key} {bundle.weights.get(key, 0):.2f}" for key in DIMENSIONS)
            ),
            styles["body"],
        )
    )
    story.append(Spacer(1, 12))
    story.append(Paragraph("Limitations", styles["h1"]))
    story.append(Paragraph(_esc(LIMITATIONS), styles["body"]))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        title="Opportunity report",
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        pageCompression=0,
    )

    def footer(canvas, _doc) -> None:
        _footer(canvas, bundle)

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _meta(bundle: ReportBundle) -> str:
    return (
        f"Run {bundle.run_id}. Exported {bundle.exported_at.date().isoformat()}. "
        f"Filters: {bundle.filters_summary}. Weights: {bundle.weights_text()}."
    )


def _summary(bundle: ReportBundle, top_n: int) -> str:
    if not bundle.areas:
        return (
            "This export has no opportunity areas to rank. The files still record the run, "
            "the date, the weights, and the filters so the empty result can be reproduced."
        )
    top = bundle.areas[0]
    extra = ""
    if len(bundle.areas) > top_n:
        extra = f" The pages that follow cover the top {top_n} of {len(bundle.areas)} areas."
    return (
        f"{len(bundle.areas)} opportunity areas are ranked. "
        f"The leading area is {top.name} "
        f"({top.composite:.2f}, {top.band}). "
        "Each area page follows the discovery example: the problem, what users remember and "
        f"forget, how they searched, where retrieval broke, the score, and why it matters.{extra}"
    )


def _area_page(area, styles) -> list:
    blocks = [
        Paragraph(f"Opportunity Area: {_esc(area.name)}", styles["title"]),
        Paragraph("Problem Summary", styles["h1"]),
        Paragraph(_esc(area.problem_summary or "Not stated."), styles["body"]),
        Paragraph("What Users Remember", styles["h1"]),
        *_bullets(area.remembered, styles),
        Paragraph("What Users Forget", styles["h1"]),
        *_bullets(area.forgotten, styles),
        Paragraph("Common Search Attempts", styles["h1"]),
        *_bullets(area.search_attempts, styles),
        Paragraph("Breakdown Point", styles["h1"]),
        Paragraph(_esc(area.breakdown_point or "Not stated."), styles["body"]),
        Paragraph("Opportunity Score", styles["h1"]),
        Paragraph(
            _esc(
                f"{area.band} ({area.composite:.2f}). "
                + ", ".join(f"{key.replace('_', ' ')} {area.scores[key]:.2f}" for key in DIMENSIONS)
            ),
            styles["body"],
        ),
        Paragraph("Why This Matters", styles["h1"]),
        Paragraph(_esc(area.why_it_matters or "Not synthesized."), styles["body"]),
        Paragraph("Representative quotes", styles["h1"]),
    ]
    if area.quotes:
        for quote, url, source in area.quotes[:8]:
            link = f" ({source}: {url})" if url else ""
            blocks.append(Paragraph(_esc(f"“{quote}”{link}"), styles["quote"]))
    else:
        blocks.append(
            Paragraph(
                "Representative quotes are withheld from this report, or none matched the filters.",
                styles["note"],
            )
        )
    blocks.append(Paragraph("Follow-up research questions", styles["h1"]))
    blocks.extend(_bullets(area.research_questions, styles))
    return blocks


def _bullets(items: list[str], styles) -> list:
    if not items:
        return [Paragraph("Not stated.", styles["body"])]
    return [Paragraph("- " + _esc(item), styles["body"]) for item in items[:8]]


def _ranked_table(bundle: ReportBundle, font: str) -> Table:
    header = ["Rank", "Opportunity area", "Score", "Band"]
    data: list[list[str]] = [header]
    for area in bundle.areas:
        data.append(
            [str(area.rank), glyph_safe(area.name, font), f"{area.composite:.2f}", area.band]
        )
    if len(data) == 1:
        data.append(["—", glyph_safe(bundle.note or "No matching data", font), "—", "—"])
    table = Table(data, colWidths=[0.6 * inch, 4.3 * inch, 0.8 * inch, 0.9 * inch])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), font),
                ("FONTNAME", (0, 1), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4b6e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d7de")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _styles(font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ExportTitle",
            parent=base["Title"],
            fontName=font,
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#1a1a1a"),
            spaceAfter=6,
        ),
        "h1": ParagraphStyle(
            "ExportH1",
            parent=base["Heading2"],
            fontName=font,
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#1f4b6e"),
            spaceBefore=8,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "ExportBody",
            parent=base["BodyText"],
            fontName=font,
            fontSize=10,
            leading=13,
            spaceAfter=2,
        ),
        "meta": ParagraphStyle(
            "ExportMeta",
            parent=base["BodyText"],
            fontName=font,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#444444"),
        ),
        "quote": ParagraphStyle(
            "ExportQuote",
            parent=base["BodyText"],
            fontName=font,
            fontSize=9,
            leading=12,
            leftIndent=8,
            textColor=colors.HexColor("#222222"),
            spaceAfter=4,
        ),
        "note": ParagraphStyle(
            "ExportNote",
            parent=base["BodyText"],
            fontName=font,
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#7a4e00"),
        ),
    }


def glyph_safe(text: str, font_name: str | None = None) -> str:
    """Keep characters the PDF font can draw. Everything else becomes a box (EXP-07)."""
    font_name = font_name or _active_font
    if font_name == "Helvetica":
        return winansi_safe(text)
    from reportlab.pdfbase import pdfmetrics

    face = getattr(pdfmetrics.getFont(font_name), "face", None)
    if face is None:
        return winansi_safe(text)
    out = []
    for char in text or "":
        if char in "\n\t" or face.charToGlyph.get(ord(char)):
            out.append(char)
        else:
            out.append("□")
    return "".join(out)


def _esc(text: str) -> str:
    return xml.escape(glyph_safe(text)).replace("\n", "<br/>")


def _footer(canvas, bundle: ReportBundle) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    label = winansi_safe(
        f"{bundle.run_id}  ·  {bundle.exported_at.date().isoformat()}  ·  {bundle.filters.audience}"
    )
    canvas.drawString(0.75 * inch, 0.4 * inch, label[:140])
    canvas.restoreState()


def _font_name() -> str:
    """Prefer a Unicode face when the machine has one. Helvetica is the fallback."""
    global _registered
    if _registered:
        return _registered
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if not path.is_file():
            continue
        pdfmetrics.registerFont(TTFont("ExportSans", str(path)))
        _registered = "ExportSans"
        return _registered
    _registered = "Helvetica"
    return _registered
