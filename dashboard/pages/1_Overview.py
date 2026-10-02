import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import streamlit as st

from dashboard.charts import category_bars, source_trend
from dashboard.data_access import areas_for, overview_for, published_context
from dashboard.ui import (
    banner,
    configure,
    current_filters,
    empty_run,
    inject_css,
    kpi_cards,
    page_header,
    run_chip,
)

configure()
inject_css()

published, notice, token = published_context()
filters = current_filters()
run_chip(published)
banner(notice)
page_header(
    "Ingestion and pipeline",
    "Corpus overview",
    "How much feedback survived cleaning, retrieval classification, and vague-memory focus.",
    filters,
)

if not published:
    empty_run()
    st.stop()

areas = areas_for(published, token, filters)
overview = overview_for(published, token, filters, len(areas))
ready = overview["ready"] or 0
retrieval = overview["retrieval"] or 0
vague = overview["vague"] or 0
ingested = overview["raw"] if overview["raw"] is not None else overview["items"]


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "—"
    return f"{part / whole:.1%} of the previous stage"


kpi_cards(
    [
        (
            "Ingested",
            f"{ingested:,}",
            "Raw items" if overview["raw"] is not None else "Items matching filters",
            False,
        ),
        ("Ready for analysis", f"{ready:,}", "Cleaned, not spam", False),
        ("Retrieval-relevant", f"{retrieval:,}", _pct(retrieval, ready), False),
        ("Vague-memory focus", f"{vague:,}", _pct(vague, retrieval) if retrieval else "—", True),
    ]
)

stages = [
    ("01", "Ingested", ingested, "Raw log" if overview["raw"] is not None else "Filtered items"),
    ("02", "Ready", ready, "Spam and empty text dropped"),
    ("03", "Retrieval", retrieval, "General or vague retrieval"),
    ("04", "Vague memory", vague, "Remembers it, cannot place it"),
    ("05", "Opportunity areas", overview["areas"], "Published, matching filters"),
]
cards = []
for number, label, value, note in stages:
    cards.append(
        f'<div class="stage"><span class="kicker">{number}</span>'
        f'<strong>{label}</strong><div class="n">{value:,}</div>'
        f'<div class="kpi-note">{note}</div></div>'
    )
st.markdown(
    '<div class="card"><h2>Ingestion to opportunity funnel</h2>'
    f'<div class="stage-grid">{"".join(cards)}</div></div>',
    unsafe_allow_html=True,
)
st.caption(
    "Category, content type, severity, confidence, and vague-memory filters apply from "
    "the retrieval stage onward. Source, platform, date, rating, and language apply throughout."
)

left, right = st.columns(2)
with left:
    if overview["trend"]:
        st.plotly_chart(source_trend(overview["trend"]), width="stretch")
    else:
        st.info("No dated items in this filter.")
with right:
    if overview["categories"]:
        st.plotly_chart(category_bars(overview["categories"]), width="stretch")
    else:
        st.info("No retrieval items in this filter.")
