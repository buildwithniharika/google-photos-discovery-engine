"""Photos Discovery dashboard. Streamlit Community Cloud main file: dashboard/app.py."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import streamlit as st

from dashboard.data_access import cached_languages, published_context
from dashboard.ui import configure, inject_css, render_sidebar

configure()
inject_css()

pages = [
    st.Page("pages/1_Overview.py", title="Overview", default=True, url_path="overview"),
    st.Page("pages/2_Compare.py", title="Compare", url_path="compare"),
    st.Page("pages/3_Detail.py", title="Detail", url_path="detail"),
    st.Page("pages/4_Evidence.py", title="Evidence", url_path="evidence"),
    st.Page("pages/5_Quality.py", title="Quality", url_path="quality"),
    st.Page("pages/6_Export.py", title="Export", url_path="export"),
]
navigation = st.navigation(pages, position="hidden")

try:
    token = published_context()[2]
    languages = cached_languages(token)
except Exception as exc:
    st.error(
        "The dashboard could not open the database. Set DATABASE_URL in .streamlit/secrets.toml "
        f"or .env. ({exc})"
    )
    st.stop()

with st.sidebar:
    st.markdown(
        '<div class="brand">'
        '<span class="photos-mark" aria-hidden="true"></span>'
        "<div>"
        '<div class="brand-title">Photos Discovery</div>'
        '<div class="brand-kicker">Internal research</div>'
        "</div></div>",
        unsafe_allow_html=True,
    )
    for page in pages:
        st.page_link(page, label=page.title)
    render_sidebar(languages)

navigation.run()
