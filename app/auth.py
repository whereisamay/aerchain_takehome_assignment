"""Single shared-password gate. The app holds a live API key, so nothing renders until this passes."""
import hmac

import streamlit as st


def require_password() -> None:
    if st.session_state.get("authed"):
        return

    expected = st.secrets.get("APP_PASSWORD") if hasattr(st, "secrets") else None
    if not expected:
        st.error("APP_PASSWORD is not configured in secrets. Refusing to start.")
        st.stop()

    st.title("Kill the Quote Spreadsheet")
    pw = st.text_input("Password", type="password")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state["authed"] = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()
