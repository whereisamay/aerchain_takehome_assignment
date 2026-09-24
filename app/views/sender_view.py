from pathlib import Path

import pandas as pd
import streamlit as st

import db
import mail
import vendors
from rfq import validate_rfq
from rfq_xlsx import render_rfq_xlsx
from state import DEMO_RFQ, ensure_rfqs
from views.common import transport_note


def render() -> None:
    st.header("RFQ Sender")
    transport_note()
    ensure_rfqs()
    rfqs = db.list_rfqs()
    ids = [r["rfq_id"] for r in rfqs]
    rid = st.selectbox("RFQ to send", ids, index=ids.index(DEMO_RFQ) if DEMO_RFQ in ids else 0,
                       format_func=lambda i: f"{i} · {next(r['title'] for r in rfqs if r['rfq_id'] == i)}")
    rfq = db.get_rfq(rid)
    problems = validate_rfq(rfq)
    if problems:
        st.error("This RFQ has problems — fix them in the RFQ Generator first:\n\n"
                 + "\n".join(f"- {p}" for p in problems))
        return

    st.markdown("**Vendors**")
    vs = vendors.list_vendors()
    sent_to = {o["vendor_code"] for o in mail.outbox() if o["rfq_id"] == rid and o["kind"] == "rfq"}
    table = pd.DataFrame([{
        "Send": v["code"] not in sent_to, "Vendor": v["code"], "Name": v["name"], "City": v["city"],
        "Email": v["email"], "Reference": ("Yes — " + v["referred_by"]) if v["has_reference"] else "No",
        "Supplied since": v["supplied_since"] or "—",
        "Already sent": "✓" if v["code"] in sent_to else "",
    } for v in vs])
    picked = st.data_editor(table, hide_index=True, width="stretch", disabled=[c for c in table if c != "Send"],
                            key=f"pick_{rid}")
    codes = list(picked.loc[picked["Send"], "Vendor"])

    with st.expander("Preview the email and attachment"):
        v0 = next((v for v in vs if v["code"] in codes), vs[0])
        st.caption(f"To: {v0['name']} <{v0['email']}> · Reply-To: {mail.reply_address(rid, v0['code'])}")
        st.text(mail._rfq_body(rfq, v0))
        st.download_button(f"📎 {rid}.xlsx", render_rfq_xlsx(rfq), file_name=f"{rid}.xlsx")

    if st.button(f"Send {rid} to {len(codes)} vendor(s)", type="primary", disabled=not codes):
        mail.send_rfq(rfq, codes)
        st.toast(f"Sent {rid} to {', '.join(codes)}")
        st.rerun()

    st.subheader("Sent")
    out = mail.outbox()
    if not out:
        st.info("Nothing sent yet.")
        return
    st.dataframe(pd.DataFrame([{
        "When": o["created_at"].replace("T", " "), "Kind": o["kind"], "Status": o["status"], "RFQ": o["rfq_id"],
        "Vendor": o["vendor_code"], "To": o["to_addr"], "Reply-To (correlation token)": o["reply_to"],
        "Subject": o["subject"]} for o in out]), hide_index=True, width="stretch")
    for o in out[:12]:
        with st.expander(f"{o['vendor_code']} · {o['subject']}"):
            st.text(o["body"])
            p = Path(o["path"])
            if p.exists():
                st.download_button("Download .eml", p.read_bytes(), file_name=p.name, key=f"eml_{o['id']}")

    with st.expander("Purchase history on record (used later to resolve 'same terms as last time')"):
        h = vendors.history()
        st.dataframe(pd.DataFrame([{
            "Vendor": r["vendor_code"], "Type": r["doc_type"], "Ref": r["ref"], "Date": r["date"],
            "Material": r["material"], "Awarded": "Yes" if r["awarded"] else "No",
            "Credit": r["terms"].get("credit") or "—", "Freight": r["terms"].get("freight") or "not recorded",
            "Note": r["note"] or ""} for r in h]), hide_index=True, width="stretch")
