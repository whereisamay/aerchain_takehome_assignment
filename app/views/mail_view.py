from pathlib import Path

import pandas as pd
import streamlit as st

import db
import mail
import vendors
from state import ensure_rfqs

LABEL = ("Mail transport is simulated. Composition, threading, ingestion and parsing are real; production "
         "swaps in an SMTP provider and an inbound webhook, and nothing above this layer changes.")
METHOD_LABEL = {"token": "① correlation token", "subject": "② subject reference", "sender": "③ sender address",
                "manual": "assigned by buyer", None: "—"}


def _vendor_names() -> dict:
    return {v["code"]: v["name"] for v in vendors.list_vendors()}


def _attachment_widgets(row: dict, key: str) -> None:
    for i, a in enumerate(row["attachments"]):
        p = Path(a["path"])
        if not p.exists():
            st.caption(f"📎 {a['name']} (file no longer on disk)")
            continue
        c1, c2 = st.columns([3, 1])
        c1.markdown(f"📎 **{a['name']}** · {a['mime']} · {a['size'] / 1024:.0f} KB")
        c2.download_button("Download", p.read_bytes(), file_name=a["name"], key=f"{key}_dl{i}")
        if a["mime"].startswith("image/"):
            st.image(str(p), width=420)


def _outbox() -> None:
    rfqs = db.list_rfqs()
    names = _vendor_names()
    with st.form("send"):
        c1, c2 = st.columns([2, 3])
        ids = [r["rfq_id"] for r in rfqs]
        rid = c1.selectbox("RFQ", ids, index=ids.index("RFQ-2026-MCH-005") if "RFQ-2026-MCH-005" in ids else 0,
                           format_func=lambda i: f"{i} · {next(r['material'] for r in rfqs if r['rfq_id'] == i)}")
        codes = c2.multiselect("Vendors", list(names), default=list(names),
                               format_func=lambda c: f"{c} · {names[c]}")
        go = st.form_submit_button("Send RFQ with xlsx pack", type="primary")
    if go and codes:
        mail.send_rfq(db.get_rfq(rid), codes)
        st.success(f"Composed and 'sent' {len(codes)} emails for {rid}. Each is a real .eml in data/outbox/.")

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


def _inbox() -> None:
    c1, c2, _ = st.columns([2, 1, 3])
    if c1.button("Simulate vendor replies arriving", type="primary",
                 help="Drops the six demo vendor emails into data/inbox, as if vendors had replied."):
        n = mail.deliver_demo_replies()
        mail.check_inbox()
        st.session_state["mail_toast"] = (f"{n} new message(s) delivered and read" if n
                                          else "Demo replies were already delivered")
        st.rerun()
    if c2.button("Check inbox"):
        mail.check_inbox()
        st.rerun()
    rows = mail.inbox()
    if not rows:
        st.info("Inbox is empty. Send the RFQ first, then simulate the replies — or drop a file in the Upload tab.")
        return
    names = _vendor_names()
    st.dataframe(pd.DataFrame([{
        "Status": "✅ matched" if r["status"] == "matched" else ("🟦 assigned" if r["status"] == "assigned"
                                                                  else "⚠️ unmatched"),
        "Vendor": f"{r['vendor_code']} · {names.get(r['vendor_code'], '')}" if r["vendor_code"] else "—",
        "RFQ": r["rfq_id"] or "—", "From": r["from_addr"], "Subject": r["subject"],
        "Attachments": ", ".join(a["name"] for a in r["attachments"]) or "(body only)",
        "Matched by": METHOD_LABEL.get(r["match_method"], r["match_method"]),
    } for r in rows]), hide_index=True, width="stretch")
    st.caption("Correlation order: ① token in the reply-to address → ② RFQ reference in the subject (vendor "
               "from sender) → ③ sender address, only when exactly one RFQ is open with that vendor. "
               "If none match, the message is not guessed — it goes to Unmatched.")
    for r in rows:
        tag = r["vendor_code"] or "?"
        with st.expander(f"{tag} · {r['from_addr']} · {r['subject']}"):
            st.caption(f"How it was matched: {r['match_detail']}")
            st.text(r["body"])
            _attachment_widgets(r, f"in{r['id']}")


def _assign_form(r: dict, key: str) -> None:
    rfq_ids = [x["rfq_id"] for x in db.list_rfqs()]
    names = _vendor_names()
    c1, c2, c3 = st.columns([2, 2, 1])
    rid = c1.selectbox("RFQ", rfq_ids, key=f"{key}_r",
                       index=rfq_ids.index("RFQ-2026-MCH-005") if "RFQ-2026-MCH-005" in rfq_ids else 0)
    vc = c2.selectbox("Vendor", list(names), key=f"{key}_v", format_func=lambda c: f"{c} · {names[c]}",
                      index=None, placeholder="Who sent this?")
    c3.write("")
    if c3.button("Assign", key=f"{key}_b", disabled=vc is None):
        mail.assign(r["id"], rid, vc)
        st.rerun()


def _unmatched() -> None:
    rows = mail.inbox("unmatched")
    if not rows:
        st.success("No unmatched messages.")
        return
    st.warning(f"{len(rows)} message(s) could not be matched to a vendor and RFQ. The system does not guess — "
               "check each one and assign it.")
    for r in rows:
        with st.container(border=True):
            st.markdown(f"**{r['subject']}** — from `{r['from_addr']}`")
            st.caption(f"Why it wasn't matched: {r['match_detail']}")
            st.text(r["body"])
            _attachment_widgets(r, f"un{r['id']}")
            _assign_form(r, f"as{r['id']}")


def _upload() -> None:
    st.caption("Drop any vendor response — an .eml, or a loose PDF / xlsx / docx / photo / text file. An .eml is "
               "correlated like any other inbound mail. A loose file has no headers to correlate on, so it goes "
               "to Unmatched unless you say who it's from.")
    names = _vendor_names()
    rfq_ids = [x["rfq_id"] for x in db.list_rfqs()]
    with st.form("upload", clear_on_submit=True):
        f = st.file_uploader("Vendor response", type=["eml", "pdf", "xlsx", "xls", "docx", "jpg", "jpeg", "png",
                                                      "txt", "csv"])
        c1, c2 = st.columns(2)
        vc = c1.selectbox("From vendor (optional)", list(names), index=None, placeholder="Leave blank to route it",
                          format_func=lambda c: f"{c} · {names[c]}")
        rid = c2.selectbox("For RFQ", rfq_ids,
                           index=rfq_ids.index("RFQ-2026-MCH-005") if "RFQ-2026-MCH-005" in rfq_ids else 0)
        go = st.form_submit_button("Add to inbox", type="primary")
    if go and f:
        r = mail.ingest_upload(f.name, f.getvalue())
        if vc and r["status"] == "unmatched":
            mail.assign(r["id"], rid, vc)
            st.success(f"Added {f.name} and assigned it to vendor {vc} / {rid}.")
        elif r["status"] == "matched":
            st.success(f"Added {f.name}; matched to vendor {r['vendor_code']} / {r['rfq_id']} "
                       f"by {METHOD_LABEL[r['match_method']]}.")
        else:
            st.warning(f"Added {f.name}. It couldn't be matched automatically, so it is in the Unmatched queue.")


def render() -> None:
    st.header("Mail")
    ensure_rfqs()
    if msg := st.session_state.pop("mail_toast", None):
        st.toast(msg)
    st.info(LABEL, icon="✉️")
    n_un = len(mail.inbox("unmatched"))
    t_out, t_in, t_un, t_up = st.tabs(["Outbox", "Inbox", f"Unmatched ({n_un})", "Upload a response"])
    with t_out:
        _outbox()
    with t_in:
        _inbox()
    with t_un:
        _unmatched()
    with t_up:
        _upload()
    with st.expander("Reset demo mail"):
        st.caption("Clears the outbox, inbox and all correlation records.")
        if st.button("Reset mail"):
            mail.reset_mail()
            st.rerun()
