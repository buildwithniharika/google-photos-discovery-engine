import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import html

import streamlit as st

from dashboard.charts import breakdown_funnel, horizontal_bars, paired_bars
from dashboard.data_access import areas_for, dossier_for, published_context, write_override
from dashboard.labels import DIMENSION_LABELS, DIMENSIONS, category_labels, titleize
from dashboard.ui import (
    band_html,
    banner,
    configure,
    current_filters,
    empty_run,
    inject_css,
    page_header,
    pm_badge,
    quote_block,
    run_chip,
)

configure()
inject_css()

published, notice, token = published_context()
filters = current_filters()
run_chip(published)
banner(notice)

if not published:
    page_header(
        "Opportunity",
        "Opportunity detail",
        "One area, with the evidence behind it.",
        filters,
    )
    empty_run()
    st.stop()

areas = areas_for(published, token, filters, active_only=False)
visible = [
    area
    for area in areas
    if area["status"] == "active" or area["area_id"] == st.query_params.get("area")
]
if not visible:
    page_header(
        "Opportunity",
        "Opportunity detail",
        "One area, with the evidence behind it.",
        filters,
    )
    st.info("No opportunity areas match these filters.")
    st.stop()

ids = [area["area_id"] for area in visible]
requested = st.query_params.get("area")
index = ids.index(requested) if requested in ids else 0
with st.container(key="area_picker"):
    st.markdown(
        '<p class="area-picker-kicker">Switch opportunity area</p>'
        '<p class="area-picker-help">Open the list to read a different problem.</p>',
        unsafe_allow_html=True,
    )
    selected_id = st.selectbox(
        "Opportunity area",
        ids,
        index=index,
        format_func=lambda area_id: next(
            area["name"] for area in visible if area["area_id"] == area_id
        ),
    )
if selected_id != requested:
    st.query_params["area"] = selected_id

area = next(item for item in visible if item["area_id"] == selected_id)
labels = category_labels()
name_badge = " " + pm_badge() if area.get("name_overridden") else ""
page_header(
    labels.get(area["category"], titleize(area["category"])),
    area["name"],
    area.get("problem_summary") or "No synthesis summary for this area.",
    filters,
)
st.markdown(name_badge, unsafe_allow_html=True)
meta = (
    f"{band_html(area['band'])} · composite {area['composite']:.2f} · "
    f"{area['match_count']} matching items · {area['vague_match_count']} vague"
)
if area.get("low_evidence_flag"):
    meta += " · Emerging, low evidence"
if area.get("merge_into"):
    meta += f" · Merge into {area['merge_into']} is pending the next cluster run"
if area.get("split_pending"):
    meta += " · A split is pending the next cluster run"
st.markdown(meta, unsafe_allow_html=True)

why = (area.get("aggregates") or {}).get("why_it_matters") or {}
if isinstance(why, dict) and why.get("text"):
    st.markdown(f"**Why it matters.** {why['text']}")

dossier = dossier_for(published, token, area["area_id"], filters)
if not dossier or not dossier.get("match_count"):
    st.info("No evidence in this area matches the current filters.")
else:
    left, right = st.columns(2)
    with left:
        if dossier["sources"]:
            st.plotly_chart(
                horizontal_bars("Source breakdown", dossier["sources"]),
                width="stretch",
            )
    with right:
        if dossier["content_types"]:
            st.plotly_chart(
                horizontal_bars("Content types", dossier["content_types"], color="#34A853"),
                width="stretch",
            )
    remembered, forgotten = paired_bars(dossier["cue_types"], dossier["forgotten"])
    cue_col, forgot_col = st.columns(2)
    with cue_col:
        st.plotly_chart(remembered, width="stretch")
    with forgot_col:
        st.plotly_chart(forgotten, width="stretch")
    attempt_col, break_col = st.columns(2)
    with attempt_col:
        if dossier["attempts"]:
            st.plotly_chart(
                horizontal_bars("Search attempts", dossier["attempts"]),
                width="stretch",
            )
        else:
            st.info("These items do not describe a search attempt.")
    with break_col:
        if dossier["breakdown"]:
            st.plotly_chart(breakdown_funnel(dossier["breakdown"]), width="stretch")

    st.subheader("What users said")
    if not dossier["quotes"]:
        st.caption("No grounded quote in the matching items.")
    for quote in dossier["quotes"]:
        quote_block(
            quote.get("evidence_quote") or "",
            quote.get("source") or "",
            quote.get("source_url") or "",
            quote.get("platform") or "",
        )

st.subheader("Score breakdown")
explanations = (area.get("inputs") or {}).get("explanations") or {}
for key in DIMENSIONS:
    label = DIMENSION_LABELS[key]
    overridden = area.get(f"{key}_overridden")
    badge = " " + pm_badge() if overridden else ""
    struck = ""
    if overridden:
        struck = f'<span class="struck">{area[f"{key}_ai"]:.2f}</span> '
    st.markdown(
        f"**{html.escape(label)}** {struck}`{area[key]:.2f}` {badge}",
        unsafe_allow_html=True,
    )
    if explanations.get(key):
        st.caption(explanations[key])
if explanations.get("composite"):
    st.caption(explanations["composite"])
if explanations.get("guardrail"):
    st.caption(explanations["guardrail"])

st.subheader("Research questions")
questions = area.get("research_questions") or []
if not questions:
    st.caption("No research questions were stored for this area.")
for number, question in enumerate(questions, start=1):
    if isinstance(question, str):
        st.markdown(f"{number}. {question}")
        continue
    st.markdown(f"**{number}. {question.get('question', '')}**")
    if question.get("evidence_gap"):
        st.caption(question["evidence_gap"])

st.subheader("PM notes and corrections")
st.caption(
    "Corrections are stored in pm_overrides. A rename, score, or note shows here immediately. "
    "Merge, split, and archive are applied on the next `discovery cluster` / `discovery score` run."
)
if area.get("pm_note"):
    st.markdown(
        f'<div class="card"><div class="kicker">PM note {pm_badge()}</div>'
        f"<p>{html.escape(area['pm_note'])}</p></div>",
        unsafe_allow_html=True,
    )

with st.form("area_curation"):
    new_name = st.text_input("Rename", value=area["name"])
    targets = [item["area_id"] for item in areas if item["area_id"] != area["area_id"]]
    merge_into = st.selectbox("Merge into", ["—"] + targets, format_func=lambda value: value)
    themes = (area.get("aggregates") or {}).get("sub_themes") or []
    split_ids = st.multiselect(
        "Split these clusters out",
        options=[theme["cluster_id"] for theme in themes],
        format_func=lambda cid: next(
            (
                f"{theme['cluster_id']} · {theme.get('label', '')}"
                for theme in themes
                if theme["cluster_id"] == cid
            ),
            cid,
        ),
    )
    note = st.text_area("PM note", value=area.get("pm_note") or "")
    score_left, score_right = st.columns(2)
    with score_left:
        leverage = st.number_input(
            "Product leverage",
            min_value=1.0,
            max_value=5.0,
            value=float(area["product_leverage"]),
            step=0.1,
        )
    with score_right:
        research = st.number_input(
            "Research value",
            min_value=1.0,
            max_value=5.0,
            value=float(area["research_value"]),
            step=0.1,
        )
    action = st.radio("Status", ["Keep", "Archive", "Restore"], horizontal=True)
    reason = st.text_input("Note for this correction")
    submitted = st.form_submit_button("Save corrections", type="primary")


def _save_area(
    area, run_id, new_name, merge_into, split_ids, note, leverage, research, action, reason
):
    if new_name.strip() and new_name.strip() != area["name"]:
        write_override(
            target_type="area",
            target_id=area["area_id"],
            field="name",
            ai_value=area.get("name_ai") or area["name"],
            override_value=new_name.strip(),
            note=reason,
            run_id=run_id,
        )
    if merge_into and merge_into != "—":
        write_override(
            target_type="area",
            target_id=area["area_id"],
            field="merge_into",
            ai_value=None,
            override_value=merge_into,
            note=reason,
            run_id=run_id,
        )
    if split_ids:
        write_override(
            target_type="area",
            target_id=area["area_id"],
            field="split_clusters",
            ai_value=None,
            override_value=split_ids,
            note=reason,
            run_id=run_id,
        )
    if note.strip() != (area.get("pm_note") or ""):
        write_override(
            target_type="area",
            target_id=area["area_id"],
            field="pm_note",
            ai_value=area.get("pm_note") or None,
            override_value=note.strip(),
            note=reason,
            run_id=run_id,
        )
    if round(float(leverage), 2) != round(float(area["product_leverage"]), 2):
        write_override(
            target_type="area",
            target_id=area["area_id"],
            field="product_leverage",
            ai_value=area.get("product_leverage_ai"),
            override_value=float(leverage),
            note=reason,
            run_id=run_id,
        )
    if round(float(research), 2) != round(float(area["research_value"]), 2):
        write_override(
            target_type="area",
            target_id=area["area_id"],
            field="research_value",
            ai_value=area.get("research_value_ai"),
            override_value=float(research),
            note=reason,
            run_id=run_id,
        )
    if action == "Archive" and area["status"] != "archived":
        write_override(
            target_type="area",
            target_id=area["area_id"],
            field="status",
            ai_value=area["status"],
            override_value="archived",
            note=reason,
            run_id=run_id,
        )
    if action == "Restore" and area["status"] != "active":
        write_override(
            target_type="area",
            target_id=area["area_id"],
            field="status",
            ai_value=area["status"],
            override_value="active",
            note=reason,
            run_id=run_id,
        )


if submitted:
    try:
        _save_area(
            area,
            published["run_id"],
            new_name,
            merge_into,
            split_ids,
            note,
            leverage,
            research,
            action,
            reason,
        )
    except ValueError as exc:
        st.error(str(exc))
    else:
        st.success(
            "Saved to pm_overrides. It stays after a refresh, a redeploy, or a pipeline re-run."
        )
        st.rerun()
