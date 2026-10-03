import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pandas as pd
import streamlit as st

from dashboard.data_access import published_context, quality_for
from dashboard.labels import LOW_CONFIDENCE, SOURCE_LABELS
from dashboard.ui import (
    banner,
    configure,
    current_filters,
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
    "Trust",
    "Can I trust this run?",
    "Run history, source errors, dedup, model spend, quote grounding, "
    "and the low-confidence queue.",
    filters,
)

quality = quality_for(published, token)
failed = quality["grounding_failed"]
total = quality["grounding_total"]
rate = failed / total if total else 0.0
kpi_cards(
    [
        ("LLM tokens", f"{quality['llm_tokens']:,}", "All stages, all runs", False),
        ("LLM cost", f"${quality['llm_cost_usd']:.2f}", "Recorded on pipeline_runs", False),
        (
            "Quote grounding failures",
            f"{rate:.1%}",
            f"{failed} of {total} checked quotes",
            rate > 0.02,
        ),
        (
            "PM corrections",
            f"{quality['override_count']}",
            f"{quality['item_corrections']} on items",
            False,
        ),
    ]
)

st.subheader("Recent runs")
if quality["runs"]:
    st.dataframe(pd.DataFrame(quality["runs"]), hide_index=True, width="stretch")
else:
    st.caption("No pipeline runs recorded yet.")

st.subheader("Runs that were not published")
if not quality.get("publish_notes"):
    st.caption("No failed publish checks in the recent history.")
for note in quality.get("publish_notes") or []:
    st.markdown(f"**{note['status']}** · `{note['run_id']}`")
    for message in note["messages"]:
        st.warning(str(message)[:500])

st.subheader("Source ingestion and errors")
if not quality["ingest"]:
    st.caption("No ingest stages recorded.")
for stage, info in quality["ingest"].items():
    source = stage.split(":", 1)[-1]
    st.markdown(f"**{SOURCE_LABELS.get(source, source)}** · {info['status']} · `{info['run_id']}`")
    if info["counts"]:
        st.json(info["counts"], expanded=False)
    if info["errors"]:
        for error in info["errors"]:
            st.warning(str(error)[:500])

st.subheader("Deduplication and hygiene")
dedup = quality.get("dedup") or {}
if not dedup:
    st.caption("No prep stage recorded.")
else:
    nested = dedup.get("dedup") if isinstance(dedup.get("dedup"), dict) else {}
    summary = {
        "Items in": dedup.get("items_in"),
        "Remaining": dedup.get("remaining"),
        "Ready for AI": dedup.get("ai_eligible"),
        "PII redactions": dedup.get("pii_redactions"),
        "Exact merges": nested.get("exact_merged"),
        "Near merges": nested.get("near_merged"),
        "Cross-source merges": nested.get("cross_source_merged"),
        "Spam flagged": nested.get("spam"),
    }
    st.dataframe(
        pd.DataFrame([{"Check": key, "Value": value} for key, value in summary.items()]),
        hide_index=True,
        width="stretch",
    )

st.subheader("Low-confidence queue")
st.caption(f"Insights with confidence under {LOW_CONFIDENCE:.2f}. Open Evidence to correct one.")
if quality["low_confidence"]:
    st.dataframe(pd.DataFrame(quality["low_confidence"]), hide_index=True, width="stretch")
else:
    st.caption("No low-confidence insights.")

st.subheader("Gold-set metrics")
st.caption(
    "Item corrections in pm_overrides are the pending gold-set additions. "
    "The figures below are the last committed evaluation reports in this repo."
)
if not quality.get("gold"):
    st.caption("No evaluation reports were found in eval/.")
for report in quality.get("gold") or []:
    with st.expander(report["name"]):
        st.markdown(report["excerpt"])
