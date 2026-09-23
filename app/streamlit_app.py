import streamlit as st

st.set_page_config(page_title="Kill the Quote Spreadsheet", layout="wide")

from auth import require_password  # noqa: E402

require_password()

from views import demand_view  # noqa: E402

pg = st.navigation([
    st.Page(demand_view.render, title="1 · Demand", url_path="demand", default=True),
])
pg.run()
