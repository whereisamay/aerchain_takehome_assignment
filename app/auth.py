"""Optional shared-password gate. If APP_PASSWORD is set in secrets, nothing renders until it is entered;
if it is not set, the app is open to anyone with the URL."""
import hmac
import os

import streamlit as st


def get_secret(name: str) -> str | None:
    """Top-level secret, then a secret nested one level under a [section], then an env var.
    Surrounding whitespace (common when pasting) is stripped."""
    value = _raw_secret(name)
    return value.strip() if value else None


def _raw_secret(name: str) -> str | None:
    try:
        if name in st.secrets:
            return str(st.secrets[name])
        for key in st.secrets:
            section = st.secrets[key]
            if hasattr(section, "keys") and name in section:
                return str(section[name])
    except Exception:  # no secrets file at all
        pass
    return os.environ.get(name)


def require_password() -> None:
    if st.session_state.get("authed"):
        return

    expected = get_secret("APP_PASSWORD")
    if not expected:
        return

    st.title("Kill the Quote Spreadsheet")
    pw = st.text_input("Password", type="password")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state["authed"] = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()
