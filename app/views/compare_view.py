import streamlit as st


def render() -> None:
    st.header("RFQ Standardiser & Comparison")
    st.info("Coming next: every matched vendor response is read by the model (any format → the same structured "
            "fields, each with a confidence score and a pointer to its source), normalised in Python (currency, "
            "unit basis, credit days, freight, certification verdict, delivery vs need-by), and laid out as one "
            "clean comparison you can question in plain English.", icon="🚧")
