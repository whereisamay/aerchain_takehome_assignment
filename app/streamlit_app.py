import subprocess
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Kill the Quote Spreadsheet", layout="wide")

from auth import require_password  # noqa: E402

require_password()

from views import compare_view, demand_view, inbox_view, rfq_view, sender_view  # noqa: E402


@st.cache_resource
def _build() -> str:
    try:
        return subprocess.check_output(["git", "log", "-1", "--format=%h · %cd", "--date=format:%d %b %H:%M"],
                                       cwd=Path(__file__).parent, text=True).strip()
    except Exception:
        return "unknown build"


pg = st.navigation([
    st.Page(demand_view.render, title="1 · Demand Planner", url_path="demand", default=True),
    st.Page(rfq_view.render, title="2 · RFQ Generator", url_path="rfqs"),
    st.Page(sender_view.render, title="3 · RFQ Sender", url_path="send"),
    st.Page(inbox_view.render, title="4 · Vendor Inbox", url_path="inbox"),
    st.Page(compare_view.render, title="5 · Standardise & Compare", url_path="compare"),
], position="top")
pg.run()
st.caption(f"Build {_build()}")
