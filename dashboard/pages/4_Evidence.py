import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pandas as pd
import streamlit as st

from dashboard.data_access import evidence_for, published_context, write_override
from dashboard.labels import SOURCE_LABELS, category_labels, retrieval_types, titleize
from dashboard.ui import (
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
show_irrelevant = st.session_state.get("ev_irrelevant", False)
filters = current_filters(show_irrelevant=show_irrelevant)
run_chip(published)
banner(notice)
page_header(
    "Evidence",
    "Evidence",
    "PII-redacted user text from the published run, with the extracted fields beside it.",
    filters,
)

if not published:
    empty_run()
    st.stop()

search = st.text_input("Search user text, quotes, or problem statements", key="ev_search")
show_irrelevant = st.checkbox("Include items marked irrelevant", key="ev_irrelevant")
filters = current_filters(show_irrelevant=show_irrelevant)
signature = (filters, search, show_irrelevant)
if st.session_state.get("ev_sig") != signature:
    st.session_state["ev_sig"] = signature
    st.session_state["ev_page"] = 1

page = int(st.session_state.get("ev_page", 1))
result = evidence_for(published, token, filters, search, page)
st.caption(f"{result['total']} items · page {result['page']} of {result['pages']}")

rows = result["rows"]
if not rows:
    st.info("No items match.")
else:
    frame = pd.DataFrame(
        [
            {
                "Item": row["item_id"][:12],
                "Area": row.get("area_name") or "",
                "Source": SOURCE_LABELS.get(row["source"], row["source"]),
                "Platform": row["platform"],
                "Date": row.get("date") or "",
                "Rating": row.get("rating") if row.get("rating") is not None else "",
                "User text": row.get("clean_text") or "",
                "Problem": row.get("problem_statement") or "",
                "Category": titleize(row.get("primary_category")),
                "Relevance": titleize(row.get("retrieval_type")),
                "Vague": row.get("vague_memory_relevance"),
                "Confidence": row.get("insight_confidence"),
                "Corrected": "PM" if row.get("corrected") else "",
                "Link": row.get("source_url") or "",
            }
            for row in rows
        ]
    )
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=420,
        column_config={"Link": st.column_config.LinkColumn("Source")},
    )

prev, next_, _ = st.columns([1, 1, 4])
with prev:
    if st.button("Previous", disabled=result["page"] <= 1):
        st.session_state["ev_page"] = result["page"] - 1
        st.rerun()
with next_:
    if st.button("Next", disabled=result["page"] >= result["pages"]):
        st.session_state["ev_page"] = result["page"] + 1
        st.rerun()

st.subheader("Correct an item")
st.caption(
    "The AI value is kept. The dashboard shows the correction, "
    "and a later pipeline run does not erase it."
)
if not rows:
    st.stop()

labels = {
    row["item_id"]: f"{row['item_id'][:8]} · {(row.get('clean_text') or '')[:80]}" for row in rows
}
item_id = st.selectbox("Item on this page", list(labels), format_func=lambda key: labels[key])
row = next(item for item in rows if item["item_id"] == item_id)
if row.get("corrected"):
    st.markdown(pm_badge() + " This item already has a PM correction.", unsafe_allow_html=True)

categories = category_labels()
current_category = row.get("primary_category") or ""
current_type = row.get("retrieval_type") or "not_retrieval"
with st.form("item_correction"):
    category = st.selectbox(
        "Problem category",
        list(categories),
        index=list(categories).index(current_category) if current_category in categories else 0,
        format_func=lambda key: categories[key],
    )
    retrieval = st.selectbox(
        "Relevance",
        list(retrieval_types()),
        index=(
            list(retrieval_types()).index(current_type) if current_type in retrieval_types() else 0
        ),
        format_func=titleize,
    )
    vague = st.slider(
        "Vague-memory relevance",
        0.0,
        1.0,
        float(row.get("vague_memory_relevance") or 0.0),
        0.05,
    )
    irrelevant = st.checkbox(
        "Mark as not a retrieval problem", value=bool(row.get("marked_irrelevant"))
    )
    note = st.text_input("Why this correction")
    save = st.form_submit_button("Save correction", type="primary")


def _ai(row: dict, field: str):
    return row[f"{field}_ai"] if f"{field}_ai" in row else row.get(field)


def _correct(
    row: dict, category: str, retrieval: str, vague: float, irrelevant: bool, note: str
) -> None:
    item_id = row["item_id"]
    if category != (row.get("primary_category") or ""):
        write_override(
            target_type="item",
            target_id=item_id,
            field="primary_category",
            ai_value=_ai(row, "primary_category"),
            override_value=category,
            note=note,
        )
    if irrelevant and not row.get("marked_irrelevant"):
        write_override(
            target_type="item",
            target_id=item_id,
            field="marked_irrelevant",
            ai_value=False,
            override_value=True,
            note=note,
        )
        retrieval = "not_retrieval"
    if not irrelevant and row.get("marked_irrelevant"):
        write_override(
            target_type="item",
            target_id=item_id,
            field="marked_irrelevant",
            ai_value=False,
            override_value=False,
            note=note,
        )
    if retrieval != (row.get("retrieval_type") or ""):
        write_override(
            target_type="item",
            target_id=item_id,
            field="retrieval_type",
            ai_value=_ai(row, "retrieval_type"),
            override_value=retrieval,
            note=note,
        )
    stored_vague = row.get("vague_memory_relevance")
    if stored_vague is None or abs(float(stored_vague) - float(vague)) >= 0.049:
        write_override(
            target_type="item",
            target_id=item_id,
            field="vague_memory_relevance",
            ai_value=_ai(row, "vague_memory_relevance"),
            override_value=round(float(vague), 2),
            note=note,
        )


if save:
    _correct(row, category, retrieval, vague, irrelevant, note)
    st.success("Saved to pm_overrides. Refresh or re-run the pipeline and this correction stays.")
    st.rerun()
