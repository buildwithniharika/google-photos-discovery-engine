import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import streamlit as st

from dashboard.data_access import _secret, areas_for, engine, published_context
from dashboard.labels import DIMENSION_LABELS, default_weights
from dashboard.ui import banner, configure, current_filters, inject_css, page_header, run_chip
from discovery.db import make_session_factory, session_scope
from discovery.export.bundle import ExportFilters, load_bundle

configure()
inject_css()

published, notice, token = published_context()
filters = current_filters()
run_chip(published)
banner(notice)
page_header(
    "Share",
    "Export the current view",
    "CSV, Google Sheets, and PDF are built in memory from this run, these weights, "
    "and these filters. Nothing is written on the server.",
    filters,
)

areas = areas_for(published, token, filters) if published else []
weights = (areas[0].get("weights") if areas else None) or default_weights()


def _bundle(audience: str):
    export_filters = ExportFilters.from_any(filters, audience=audience)
    with session_scope(make_session_factory(engine())) as session:
        return load_bundle(session, published["run_id"], export_filters)


def _credentials() -> dict | None:
    try:
        block = st.secrets.get("gcp_service_account")
    except Exception:
        return None
    if not block:
        return None
    data = {key: block[key] for key in block}
    if not data.get("client_email") or not data.get("private_key"):
        return None
    return data


left, mid, right = st.columns(3)
with left:
    st.markdown(
        '<div class="card accent-blue"><h2>CSV</h2>'
        '<p class="page-sub">Opportunities and evidence.</p></div>',
        unsafe_allow_html=True,
    )
    csv_external = st.checkbox("External CSV", help="Omit sensitive quotes and names.")
    if published and st.button("Prepare CSV", key="prep_csv"):
        with st.spinner("Building CSV…"):
            from discovery.export.csv_export import csv_bytes

            opportunities, evidence = csv_bytes(_bundle("external" if csv_external else "internal"))
            st.session_state["csv_files"] = (opportunities, evidence)
    files = st.session_state.get("csv_files")
    if files:
        st.download_button(
            "Download opportunities.csv",
            data=files[0],
            file_name="opportunities.csv",
            mime="text/csv",
        )
        st.download_button(
            "Download evidence.csv",
            data=files[1],
            file_name="evidence.csv",
            mime="text/csv",
        )
with mid:
    st.markdown(
        '<div class="card accent-red"><h2>Google Sheets</h2>'
        '<p class="page-sub">Opportunities, evidence, weights, methodology.</p></div>',
        unsafe_allow_html=True,
    )
    sheet_id = st.text_input(
        "Spreadsheet id",
        value="",
        help="Leave empty to create a sheet owned by the service account. "
        "Or paste an id shared with that account as Editor.",
    )
    sheets_external = st.checkbox("External Sheet", help="Omit sensitive quotes and names.")
    if published and st.button("Open a Sheet", key="open_sheet"):
        creds = _credentials()
        if not creds:
            st.error(
                "Add [gcp_service_account] to the app secrets. "
                "Share the target spreadsheet with that account's email."
            )
        else:
            with st.spinner("Writing the spreadsheet…"):
                from discovery.export.sheets_export import SheetsError, export_to_sheets

                try:
                    url = export_to_sheets(
                        _bundle("external" if sheets_external else "internal"),
                        credentials=creds,
                        spreadsheet_id=sheet_id.strip() or None,
                    )
                except SheetsError as exc:
                    st.error(str(exc))
                else:
                    link, _, warning = url.partition("\n")
                    st.success("Sheet is ready.")
                    if link:
                        st.markdown(f"[Open the spreadsheet]({link})")
                    if warning:
                        st.warning(warning)
with right:
    st.markdown(
        '<div class="card accent-yellow"><h2>PDF</h2>'
        '<p class="page-sub">Executive brief, one page per top area.</p></div>',
        unsafe_allow_html=True,
    )
    st.caption("The PDF omits high-stakes and grief quotes, and redacts some names.")
    if published and st.button("Prepare PDF", key="prep_pdf"):
        with st.spinner("Building the report…"):
            from discovery.export.pdf_report import build_pdf

            st.session_state["pdf_bytes"] = build_pdf(_bundle("external"))
    pdf = st.session_state.get("pdf_bytes")
    if pdf:
        st.download_button(
            "Download PDF",
            data=pdf,
            file_name="opportunity_report.pdf",
            mime="application/pdf",
        )

if not published:
    st.info("Publish a run with `discovery score` before exporting.")

st.subheader("Reproducibility")
st.markdown(
    f"- **Run:** `{published['run_id'] if published else '—'}`\n"
    f"- **Filters:** {filters.summary()}\n"
    f"- **Areas in view:** {len(areas)}"
)
st.markdown("**Weights**")
for key, value in weights.items():
    st.markdown(f"- {DIMENSION_LABELS.get(key, key)}: `{float(value):.2f}`")
st.caption(
    "A shared export carries this run, these weights, and these filters "
    "so someone else can reproduce it."
)

st.subheader("Run pipeline now")
token = _secret("GITHUB_TOKEN")
repository = _secret("GITHUB_REPOSITORY") or "buildwithniharika/google-photos-discovery-engine"
if not token:
    st.caption(
        "Add a fine-grained GitHub token with actions:write only as GITHUB_TOKEN in secrets "
        "to start the weekly pipeline from here. Until then, start pipeline.yml in GitHub Actions."
    )
    st.button("Run pipeline now", disabled=True)
elif st.button("Run pipeline now"):
    from discovery.export.dispatch import DispatchError, dispatch_workflow

    try:
        dispatch_workflow(token=token, repository=repository)
    except DispatchError as exc:
        st.error(str(exc))
    else:
        st.success(
            "pipeline.yml has been started. This page keeps the last published run "
            "until the new one passes its checks. A failure shows on Data Quality."
        )
