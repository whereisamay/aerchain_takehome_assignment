"""Single shared-password gate. The app holds a live API key, so nothing renders until this passes."""
import hmac
import os

import streamlit as st


def get_secret(name: str) -> str | None:
    """Top-level secret, then a secret nested one level under a [section], then an env var."""
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


def _secret_names() -> list[str]:
    try:
        return list(st.secrets.keys())
    except Exception:
        return []


def require_password() -> None:
    if st.session_state.get("authed"):
        return

    expected = get_secret("APP_PASSWORD")
    if not expected:
        names = _secret_names()
        st.error("APP_PASSWORD is not configured in secrets. Refusing to start.")
        st.caption(f"Secret names visible to the app: {', '.join(names) if names else 'none'}")
        st.stop()

    st.title("Kill the Quote Spreadsheet")
    pw = st.text_input("Password", type="password")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state["authed"] = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()
