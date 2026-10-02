"""Shared chrome: theme, sidebar filters, banners, and small HTML blocks."""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitAPIException

from dashboard.filters import Filters, filters_from_state
from dashboard.labels import (
    PLATFORMS,
    SCOPE_LABELS,
    SOURCE_LABELS,
    category_labels,
    content_types,
    source_label,
    titleize,
)

_CSS_PATH = Path(__file__).with_name("theme.css")


def configure() -> None:
    try:
        st.set_page_config(
            page_title="Photos Discovery",
            layout="wide",
            initial_sidebar_state="expanded",
        )
    except StreamlitAPIException:
        return


def inject_css() -> None:
    st.markdown(f"<style>{_CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def current_filters(*, show_irrelevant: bool = False) -> Filters:
    return filters_from_state(st.session_state, show_irrelevant=show_irrelevant)


def render_sidebar(languages: list[str]) -> Filters:
    """Global filters. Call from inside `with st.sidebar`. The same keys apply on every page."""
    defaults = {
        "f_sources": list(SOURCE_LABELS),
        "f_platforms": [],
        "f_categories": [],
        "f_severity": (1, 5),
        "f_stakes": False,
        "f_scope": "all",
        "f_vague": 0.0,
        "f_content": [],
        "f_confidence": 0.0,
        "f_dates": (),
        "f_rating": (1, 5),
        "f_unrated": True,
        "f_languages": [],
    }
    if st.session_state.pop("_reset_filters", False) or "f_sources" not in st.session_state:
        for key, value in defaults.items():
            st.session_state[key] = value

    st.markdown('<p class="filters-title">Filters</p>', unsafe_allow_html=True)
    st.multiselect(
        "Source",
        list(SOURCE_LABELS),
        format_func=lambda key: SOURCE_LABELS[key],
        key="f_sources",
    )
    st.multiselect("Platform", list(PLATFORMS), key="f_platforms")
    labels = category_labels()
    st.multiselect(
        "Problem category",
        list(labels),
        format_func=lambda key: labels[key],
        key="f_categories",
    )
    st.slider("Severity", min_value=1, max_value=5, key="f_severity")
    st.checkbox("High stakes only", key="f_stakes")
    st.radio(
        "Vague-memory relevance",
        list(SCOPE_LABELS),
        format_func=lambda key: SCOPE_LABELS[key],
        key="f_scope",
        horizontal=True,
    )
    st.slider("Minimum vague relevance", min_value=0.0, max_value=1.0, step=0.05, key="f_vague")
    st.multiselect(
        "Content type",
        list(content_types()),
        format_func=titleize,
        key="f_content",
    )
    st.slider("Minimum confidence", min_value=0.0, max_value=1.0, step=0.05, key="f_confidence")
    st.date_input("Date range", key="f_dates")
    st.slider("Rating", min_value=1, max_value=5, key="f_rating")
    st.checkbox("Include unrated", key="f_unrated")
    st.multiselect("Language", languages, key="f_languages")
    filters = current_filters()
    st.caption(f"{filters.active_count()} active" if filters.active_count() else "No filters")
    if st.button("Clear all filters", width="stretch"):
        st.session_state["_reset_filters"] = True
        st.rerun()
    st.divider()
    st.caption("Internal PM tool · Confidential")
    return filters


def run_chip(published: dict | None) -> None:
    if not published:
        return
    when = published.get("published_at")
    if hasattr(when, "strftime"):
        label = when.strftime("%d %b %Y")
    else:
        label = str(when)[:10] if when else "published"
    run_id = html.escape(str(published["run_id"]))
    st.markdown(
        f'<div class="run-chip"><span>Published run · {html.escape(label)}</span>'
        f'<span class="run-id">{run_id}</span></div>',
        unsafe_allow_html=True,
    )


def banner(info: dict | None) -> None:
    if not info:
        return
    kind = html.escape(info.get("kind", "failed"))
    st.markdown(
        f'<div class="banner {kind}">{html.escape(info["text"])}</div>',
        unsafe_allow_html=True,
    )


def page_header(kicker: str, title: str, subtitle: str, filters: Filters) -> None:
    st.markdown(
        '<div class="page-head">'
        f'<div class="kicker">{html.escape(kicker)}</div>'
        f'<h1 class="page-title">{html.escape(title)}</h1>'
        f'<p class="page-sub">{html.escape(subtitle)}</p>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.caption(filters.summary())


def kpi_cards(cards: list[tuple[str, str, str, bool]]) -> None:
    parts = []
    for label, value, note, accent in cards:
        klass = "kpi focus" if accent else "kpi"
        value_class = "kpi-value accent" if accent else "kpi-value"
        parts.append(
            f'<div class="{klass}"><div class="kpi-label">{html.escape(label)}</div>'
            f'<div class="{value_class}">{html.escape(value)}</div>'
            f'<div class="kpi-note">{html.escape(note)}</div></div>'
        )
    st.markdown(f'<div class="kpi-grid">{"".join(parts)}</div>', unsafe_allow_html=True)


def band_html(band: str) -> str:
    slug = html.escape((band or "low").lower())
    return f'<span class="band band-{slug}">{html.escape(band or "—")}</span>'


def pm_badge() -> str:
    return '<span class="pm-badge">PM</span>'


def quote_block(text: str, source: str, url: str, platform: str) -> None:
    safe = html.escape(text)
    meta = html.escape(f"{source_label(source)} · {platform}")
    link = (
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">Source</a>'
        if url
        else ""
    )
    st.markdown(
        f'<blockquote class="quote"><p>{safe}</p><footer>{meta} {link}</footer></blockquote>',
        unsafe_allow_html=True,
    )


def empty_run() -> None:
    st.info("Publish a scored run to fill this page. Nothing from an in-progress run is shown.")
