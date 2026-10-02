import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import html

import pandas as pd
import streamlit as st

from dashboard.charts import bubble, radar
from dashboard.data_access import areas_for, published_context
from dashboard.labels import (
    DIMENSION_LABELS,
    DIMENSIONS,
    category_labels,
    default_weights,
    score_bands,
)
from dashboard.ranking import live_rank
from dashboard.ui import (
    band_html,
    banner,
    configure,
    current_filters,
    empty_run,
    inject_css,
    page_header,
    pm_badge,
    run_chip,
)

configure()
inject_css()

published, notice, token = published_context()
filters = current_filters()
run_chip(published)
banner(notice)
page_header(
    "Decision",
    "Which problem should we research first?",
    "Rank uses the six scores and the weights below. "
    "Filters keep areas that still have matching evidence.",
    filters,
)

if not published:
    empty_run()
    st.stop()

base = areas_for(published, token, filters)
if not base:
    st.info("No opportunity areas match these filters.")
    st.stop()

published_weights = base[0].get("weights") or default_weights()
if st.session_state.pop("_reset_weights", False) or "w_frequency" not in st.session_state:
    for key, value in published_weights.items():
        st.session_state[f"w_{key}"] = float(value)

high, medium = score_bands()
weights = {
    key: float(st.session_state.get(f"w_{key}", published_weights.get(key, 0.1)))
    for key in DIMENSIONS
}
# Sliders are created below; seed above so the first paint uses published weights.
ranked = live_rank(base, weights, high=high, medium=medium)

top = ranked[:3]
columns = st.columns(3)
accents = ("blue", "red", "yellow")
for index, (column, area) in enumerate(zip(columns, top, strict=False)):
    with column:
        name = html.escape(area["name"])
        emerging = " · Emerging" if area.get("low_evidence_flag") else ""
        badge = " " + pm_badge() if area.get("name_overridden") else ""
        accent = accents[index % len(accents)]
        st.markdown(
            f'<div class="top-card accent-{accent}">'
            f'<div class="rank">#{area["rank"]}{emerging}</div>'
            f"<h3>{name}</h3>"
            f'<div class="kpi-value">{area["composite_live"]:.2f}</div>'
            f"{band_html(area['band_live'])}{badge}"
            f'<div class="kpi-note">{area["match_count"]} matching · '
            f"{area['vague_match_count']} vague</div></div>",
            unsafe_allow_html=True,
        )

names = category_labels()
table = pd.DataFrame(
    [
        {
            "Rank": area["rank"],
            "Opportunity": area["name"],
            "Category": names.get(area["category"], area["category"]),
            "Composite": area["composite_live"],
            "Band": area["band_live"],
            "Items": area["match_count"],
            "Vague": area["vague_match_count"],
            "Low evidence": "Yes" if area["low_evidence_flag"] else "",
            "PM": "Yes"
            if any(
                area.get(f"{key}_overridden")
                for key in ("product_leverage", "research_value", "name")
            )
            else "",
            **{DIMENSION_LABELS[key]: area[key] for key in DIMENSIONS},
        }
        for area in ranked
    ]
)
st.dataframe(table, hide_index=True, width="stretch", height=380)

choice = st.selectbox(
    "Open an area",
    options=[area["area_id"] for area in ranked],
    format_func=lambda area_id: next(area["name"] for area in ranked if area["area_id"] == area_id),
)
if st.button("Open detail", type="primary"):
    st.query_params["area"] = choice
    st.switch_page("pages/3_Detail.py")

chart_left, chart_right = st.columns(2)
picked = st.multiselect(
    "Areas on the radar",
    options=[area["area_id"] for area in ranked],
    default=[area["area_id"] for area in ranked[:3]],
    format_func=lambda area_id: next(area["name"] for area in ranked if area["area_id"] == area_id),
)
selected = [area for area in ranked if area["area_id"] in set(picked)]
with chart_left:
    if selected:
        st.plotly_chart(radar(selected), width="stretch")
with chart_right:
    st.plotly_chart(bubble(ranked), width="stretch")
    st.caption("Bubble size is evidence quality. The number is the current rank.")

st.markdown('<div class="card"><h2>Weight sensitivity</h2>', unsafe_allow_html=True)
st.caption(
    "Sliders re-rank immediately. They are not saved. Reset returns to the published weights."
)
slider_cols = st.columns(3)
for index, key in enumerate(DIMENSIONS):
    with slider_cols[index % 3]:
        st.slider(
            DIMENSION_LABELS[key],
            min_value=0.05,
            max_value=0.50,
            step=0.05,
            key=f"w_{key}",
        )
normalized = ranked[0]["weights_live"]
st.caption(
    "Normalized weights: "
    + ", ".join(f"{DIMENSION_LABELS[key]} {normalized[key]:.2f}" for key in DIMENSIONS)
)
if st.button("Reset to published weights"):
    st.session_state["_reset_weights"] = True
    st.rerun()
st.markdown("</div>", unsafe_allow_html=True)
