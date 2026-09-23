import subprocess
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Kill the Quote Spreadsheet", layout="wide")

from auth import require_password  # noqa: E402

require_password()

from views import demand_view, mail_view, rfq_view, vendors_view  # noqa: E402


@st.cache_resource
def _build() -> str:
    try:
        return subprocess.check_output(["git", "log", "-1", "--format=%h · %cd", "--date=format:%d %b %H:%M"],
                                       cwd=Path(__file__).parent, text=True).strip()
    except Exception:
        return "unknown build"


pg = st.navigation([
    st.Page(demand_view.render, title="1 · Demand", url_path="demand", default=True),
    st.Page(rfq_view.render, title="2 · RFQs", url_path="rfqs"),
    st.Page(vendors_view.render, title="3 · Vendors", url_path="vendors"),
    st.Page(mail_view.render, title="4 · Mail", url_path="mail"),
], position="top")
pg.run()
st.caption(f"Build {_build()}")
