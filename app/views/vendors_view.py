import pandas as pd
import streamlit as st

import vendors


def render() -> None:
    st.header("Vendors")
    st.caption("Vendor master and the buyer's own purchase history. Profiles only — what each vendor "
               "quoted comes exclusively from reading their response documents.")
    vs = vendors.list_vendors()
    st.dataframe(pd.DataFrame([{
        "Vendor": v["code"], "Name": v["name"], "City": v["city"], "Email": v["email"],
        "Reference": "Yes" if v["has_reference"] else "No",
        "Referred by": v["referred_by"] or "—",
        "Supplied since": v["supplied_since"] or "No supply history",
    } for v in vs]), hide_index=True, width="stretch")

    st.subheader("Purchase history on record")
    h = vendors.history()
    st.dataframe(pd.DataFrame([{
        "Vendor": r["vendor_code"], "Type": r["doc_type"], "Ref": r["ref"], "Date": r["date"],
        "Material": r["material"], "Awarded": "Yes" if r["awarded"] else "No",
        "Credit": r["terms"].get("credit") or "—", "Freight": r["terms"].get("freight") or "not recorded",
        "Note": r["note"] or "",
    } for r in h]), hide_index=True, width="stretch")
    missing = [v["code"] for v in vs if not any(r["vendor_code"] == v["code"] for r in h)]
    st.caption(f"No records at all for vendor(s) {', '.join(missing)}.")
