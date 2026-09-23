import streamlit as st

st.set_page_config(page_title="Kill the Quote Spreadsheet", layout="wide")

from auth import require_password  # noqa: E402

require_password()

st.title("Kill the Quote Spreadsheet")
st.write("Hello, world. Password gate passed.")
