import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import streamlit as st

from dashboard.data_access import areas_for, published_context
from dashboard.labels import DIMENSION_LABELS, default_weights
from dashboard.ui import banner, configure, current_filters, inject_css, page_header, run_chip

configure()
inject_css()

published, notice, token = published_context()
filters = current_filters()
run_chip(published)
banner(notice)
page_header(
    "Share",
    "Export the current view",
    "CSV, Google Sheets, and PDF are the next phase. "
    "This page already records what those files will stamp.",
    filters,
)

st.info(
    "Downloads are not wired yet. Phase 8 adds CSV, Google Sheets, and PDF from this same run, "
    "weights, and filters, generated in memory."
)

left, mid, right = st.columns(3)
with left:
    st.markdown(
        '<div class="card accent-blue"><h2>CSV</h2>'
        '<p class="page-sub">Opportunities and evidence.</p></div>',
        unsafe_allow_html=True,
    )
    st.button("Download CSV", disabled=True)
with mid:
    st.markdown(
        '<div class="card accent-red"><h2>Google Sheets</h2>'
        '<p class="page-sub">Opportunities, evidence, weights, methodology.</p></div>',
        unsafe_allow_html=True,
    )
    st.button("Open a Sheet", disabled=True)
with right:
    st.markdown(
        '<div class="card accent-yellow"><h2>PDF</h2>'
        '<p class="page-sub">Executive brief, one page per top area.</p></div>',
        unsafe_allow_html=True,
    )
    st.button("Download PDF", disabled=True)

st.subheader("Reproducibility")
areas = areas_for(published, token, filters) if published else []
weights = (areas[0].get("weights") if areas else None) or default_weights()
st.markdown(
    f"- **Run:** `{published['run_id'] if published else '—'}`\n"
    f"- **Filters:** {filters.summary()}\n"
    f"- **Areas in view:** {len(areas)}"
)
st.markdown("**Weights**")
for key, value in weights.items():
    st.markdown(f"- {DIMENSION_LABELS.get(key, key)}: `{float(value):.2f}`")
st.caption(
    "A shared export has to carry this run, these weights, and these filters "
    "so someone else can reproduce it."
)
