import streamlit as st

import vendors

TRANSPORT_LABEL = ("Mail transport is simulated. Composition, threading, ingestion and parsing are real; production "
                   "swaps in an SMTP provider and an inbound webhook, and nothing above this layer changes.")


def vendor_names() -> dict:
    return {v["code"]: v["name"] for v in vendors.list_vendors()}


def transport_note() -> None:
    st.info(TRANSPORT_LABEL, icon="✉️")
