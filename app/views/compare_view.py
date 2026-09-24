from datetime import date
from io import BytesIO

import pandas as pd
import streamlit as st

import analysis
import db
import sources
from extract import REVIEW_THRESHOLD
from llm import model_name
from master_data import FX_AS_OF, FX_RATES, FX_SOURCE
from recommend import verdicts
from state import DEMO_RFQ, current_plan, ensure_rfqs
from views.common import vendor_names

STATE_STYLE = {
    "clean": "",
    "assumed": "background-color: rgba(66, 133, 244, 0.16)",
    "review": "background-color: rgba(251, 188, 4, 0.30)",
    "buyer": "background-color: rgba(171, 71, 188, 0.28)",
    "missing": "background-color: rgba(158, 158, 158, 0.28); color: #777",
}
VERDICT_STYLE = {"Met": "color: #1e8e3e; font-weight: 600", "Needs review": "color: #b06000; font-weight: 600",
                 "Not met": "color: #d93025; font-weight: 600"}
LEGEND = ("Cells: ⬜ clean — stated, read with confidence · 🟦 assumed — converted or assumption logged · "
          "🟨 needs review — low confidence or a judgement call · 🟪 buyer must decide · "
          "⬛ missing — the vendor didn't say (shown as —, never 0).  Confidence = mean over these six "
          "parameters of (model read confidence × state: clean 1, assumed 0.85, review 0.6, buyer 0.3, missing 0).")


def _money(x) -> str:
    return "—" if x is None else f"₹{x:,.2f}"


def _pick_responses(rfq_id: str) -> list[dict]:
    msgs = analysis.responses_for(rfq_id)
    if not msgs:
        st.info("No vendor responses are matched to this RFQ yet. Send it from the RFQ Sender and bring replies in "
                "through the Vendor Inbox (assign any Unmatched ones).")
        return []
    names = vendor_names()
    table = pd.DataFrame([{
        "Analyse": True, "Vendor": m["vendor_code"], "Name": names.get(m["vendor_code"], ""),
        "Format": ", ".join(a["name"].rsplit(".", 1)[-1].upper() for a in m["attachments"]) or "email body",
        "Document": ", ".join(a["name"] for a in m["attachments"]) or "(email body only)",
        "Extracted": m["extracted_at"] or "not yet", "_id": m["id"],
    } for m in msgs])
    picked = st.data_editor(table, hide_index=True, width="stretch", key=f"pick_{rfq_id}",
                            column_config={"_id": None},
                            disabled=[c for c in table if c != "Analyse"])
    chosen = [m for m in msgs if m["id"] in set(picked.loc[picked["Analyse"], "_id"])]
    todo = [m for m in chosen if not m["extracted"]]
    c1, c2, _ = st.columns([2, 2, 3])
    run = c1.button(f"Extract {len(todo)} new response(s)", type="primary", disabled=not todo)
    rerun = c2.button(f"Re-extract all {len(chosen)} selected", disabled=not chosen)
    if run or rerun:
        batch = chosen if rerun else todo
        with st.status(f"Reading {len(batch)} response(s) with {model_name()} — in parallel, ~30-120 s…",
                       expanded=True) as status:
            done = []

            def tick(m, err):
                done.append(m)
                status.write(f"{'✅' if not err else '❌'} Vendor {m['vendor_code']}"
                             + (f" — {err}" if err else "") + f"  ({len(done)}/{len(batch)})")

            results = analysis.run_extractions(batch, on_done=tick)
            errs = [e for _, e in results if e]
            status.update(label=f"Extracted {len(batch) - len(errs)}/{len(batch)}", state="error" if errs else
                          "complete")
        st.rerun()
    return [m for m in chosen if m["extracted"]]


def _flags(result: dict) -> None:
    for f in result["flags"]:
        with st.container(border=True):
            st.markdown(f"🟪 **Buyer decision needed — Vendor {f['vendor']}** wrote “{f['quote']}” "
                        f"(_{f['source']}_)  \n**What our records show:** {f['evidence']}  \n"
                        f"**Affects:** {', '.join(f['affects'])} — left blank rather than guessed.")
            existing = analysis.buyer_inputs(f["mail_id"])
            with st.form(f"flag_{f['mail_id']}"):
                c1, c2, c3 = st.columns([1, 1, 2])
                days = c1.number_input("Credit days to use", min_value=-90, max_value=180, step=5,
                                       value=int(float(existing.get("credit_days", 0))))
                freight = c2.selectbox("Freight basis", ["ex_works", "delivered"],
                                       index=["ex_works", "delivered"].index(existing.get("freight", "ex_works")))
                note = c3.text_input("Basis for this answer", value=existing.get("credit_note", ""),
                                     placeholder="e.g. confirmed by phone with Rakesh, 2 Oct")
                a, b = st.columns([1, 3])
                if a.form_submit_button("Use these terms", type="primary"):
                    analysis.set_buyer_input(f["mail_id"], "credit_days", str(days))
                    analysis.set_buyer_input(f["mail_id"], "freight", freight)
                    analysis.set_buyer_input(f["mail_id"], "credit_note", note or "buyer decision")
                    st.rerun()
            st.caption("Or leave it open and ask the vendor — the follow-up draft is on the roadmap (step 9).")
    answered = {m for m in result["docs"] if analysis.buyer_inputs(m)}
    for mid in answered:
        bi = analysis.buyer_inputs(mid)
        c1, c2 = st.columns([5, 1])
        c1.info(f"Vendor {result['docs'][mid]['msg']['vendor_code']}: using buyer-supplied terms — "
                f"{bi.get('credit_days')} days credit, {bi.get('freight', '').replace('_', '-')} "
                f"({bi.get('credit_note', '')}).", icon="🟪")
        if c2.button("Undo", key=f"undo_{mid}"):
            analysis.clear_buyer_inputs(mid)
            st.rerun()


def _conf_style(score: float | None) -> str:
    if score is None:
        return STATE_STYLE["missing"]
    if score >= 0.85:
        return "background-color: rgba(52, 168, 83, 0.18)"
    if score >= 0.65:
        return STATE_STYLE["review"]
    return "background-color: rgba(234, 67, 53, 0.22)"


VERDICT_CELL = {"pick": "background-color: rgba(52, 168, 83, 0.22); font-weight: 600",
                "viable": "", "excluded": "color: #9aa0a6"}


def _matrix(result: dict, rfq: dict, single_material: bool, material: str | None):
    vd = verdicts(result["rows"], rfq)
    rows = [r for r in result["rows"] if material is None or r["material"] == material]
    rows = sorted(rows, key=lambda r: (r["line"], vd.get((r["line"], r["vendor"]), {}).get("rank", 99)))
    disp, styles = [], []
    for r in rows:
        v = vd.get((r["line"], r["vendor"]), {"label": "—", "kind": "excluded"})
        item = r["variant"] if single_material else f"{r['material']} · {r['variant']}".replace(" · —", "")
        credit = ("—" if r["credit_days"] is None else f"{r['credit_days']:g} days") + (
            f" (+{r['discount']})" if r["discount"] else "")
        price = _money(r["price_inr"])
        if r["price_inr"] is not None and r["landed"] is False:
            price += " · ex-works"
        delivery = ("—" if r["lead_days"] is None else
                    ("Ex-stock" if r["lead_days"] == 0 else f"{r['lead_days']} days") + f" · {r['delivery']}")
        disp.append({
            "Item": item, "Vendor": f"{r['vendor']} · {r['vendor_name']}", "Recommendation": v["label"],
            "Availability": r["availability"], "Price ₹/unit": price, "Credit period (DSO)": credit,
            "Quality standards met": r["cert"], "Delivery time": delivery, "Reference": r["reference"],
            "Confidence": f"{r['score']:.0%}",
        })
        styles.append({"Availability": r["avail_state"], "Price ₹/unit": r["price_state"],
                       "Credit period (DSO)": r["credit_state"], "Delivery time": r["delivery_state"],
                       "Quality standards met": r["cert"], "Confidence": r["score"], "Recommendation": v["kind"]})
    df = pd.DataFrame(disp)
    st.session_state["_matrix_rows"] = rows

    def style(_):
        out = pd.DataFrame("", index=df.index, columns=df.columns)
        for i, st_ in enumerate(styles):
            for col, state in st_.items():
                if col == "Quality standards met":
                    out.loc[i, col] = VERDICT_STYLE.get(state, "")
                elif col == "Confidence":
                    out.loc[i, col] = _conf_style(state)
                elif col == "Recommendation":
                    out.loc[i, col] = VERDICT_CELL[state]
                else:
                    out.loc[i, col] = STATE_STYLE[state]
            if str(df.loc[i, "Delivery time"]).split("· ")[-1].startswith("LATE"):
                out.loc[i, "Delivery time"] += "; color: #d93025; font-weight: 600"
        return out

    return df, df.style.apply(style, axis=None)


def _trace(row: dict, result: dict) -> None:
    doc = result["docs"][row["mail_id"]]
    msg, raw = doc["msg"], doc["raw"]
    st.markdown(f"#### Source trace — Vendor {row['vendor']} · {row['material']} · {row['variant']}")
    st.caption(f"Freight: {row['freight']} · Quoted as: {row['price_original'] or '—'} · "
               f"Qty required: {row['qty_required']}")
    st.caption(f"Document(s): {', '.join(doc['ext']['meta']['documents'])} · extracted by "
               f"{doc['ext']['meta']['model']} in {doc['ext']['meta']['seconds']} s · mapping {row['mapping'] or '—'}")
    labels = {"unit_price": "Unit price", "currency": "Currency", "units_per_price": "Units per priced unit",
              "qty_offered": "Qty offered", "availability": "Availability", "lead_time": "Lead time",
              "lead_time_basis": "Lead-time basis", "balance": "Balance", "declined": "Declined"}
    fields = [(labels.get(k, k), f) for k, f in row["fields"].items()]
    fields += [("Credit terms", raw["credit"]["text"]), ("Freight terms", raw["freight"]["text"])]
    for c in raw["cert_assessment"]:
        if c["material"] in (row["material"], "") or True:
            fields.append((f"Cert · {c['required']} ({c['match'].replace('_', ' ')})", c["vendor_standard"]))
            if c["expiry"]["value"]:
                fields.append((f"Cert expiry · {c['required']}", c["expiry"]))
    tbl = pd.DataFrame([{
        "Field": name, "Value": f.get("value") if f.get("value") is not None else "— (not stated)",
        "Source": f.get("source") or "—",
        "Confidence": "—" if f.get("value") is None else f"{float(f.get('confidence') or 0):.0%}",
        "Assumption": f.get("assumption") or "",
    } for name, f in fields if f])
    sel = st.dataframe(tbl, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                       key=f"trace_{row['mail_id']}_{row['line']}")
    pick = sel.selection.rows[0] if sel.selection.rows else 0
    name, f = fields[pick]
    st.markdown(f"**{name}** — _{f.get('source') or 'no source'}_")
    loc = sources.locate(msg, f)
    if loc["kind"] == "image":
        full, crop = sources.image_region(loc["path"], loc["region"])
        c1, c2 = st.columns([1, 1])
        c1.image(full, caption="Region the value was read from (outlined)")
        c2.image(crop, caption="Zoomed")
    elif loc["kind"] == "image_full":
        st.image(loc["path"], width=500, caption="No region given; whole photo shown")
    elif loc["kind"] == "pdf":
        img = sources.pdf_page_png(loc["path"], loc["page"])
        if img:
            st.image(img, caption=f"Page {loc['page']}", width=650)
    elif loc["kind"] == "xlsx" and loc.get("grid"):
        st.dataframe(pd.DataFrame(loc["grid"][1:], columns=[str(x) or " " for x in loc["grid"][0]])
                     if len(loc["grid"]) > 1 else pd.DataFrame(loc["grid"]), hide_index=True)
        st.caption("▶ marks the cited cell")
    elif loc["kind"] == "text" and loc.get("text"):
        st.markdown(f"> {loc['text']}")
    else:
        st.caption("Select a field above to see it in the document.")
    st.caption("Select a field in the table to jump to it in the document.")


def _review(result: dict) -> None:
    q = result["queue"]
    if not q:
        st.success("Nothing waiting for review.")
        return
    st.caption(f"Values the model read with confidence below {REVIEW_THRESHOLD:.0%}. Nothing is auto-accepted: "
               "confirm each one, or correct it.")
    by_vendor = {}
    for it in q:
        by_vendor.setdefault((it["vendor"], it["mail_id"]), []).append(it)
    cols = st.columns(max(len(by_vendor), 1))
    for col, ((v, mid), items) in zip(cols, sorted(by_vendor.items())):
        if col.button(f"Confirm all {len(items)} for {v}", key=f"rv_all_{mid}"):
            for it in items:
                analysis.set_review(mid, it["field"], "confirm")
            st.rerun()
    for it in q:
        with st.container(border=True):
            c1, c2 = st.columns([3, 2])
            c1.markdown(f"**Vendor {it['vendor']}** · `{it['field']}` = **{it['value']}** "
                        f"({float(it['confidence']):.0%})  \n_source: {it['source']}_"
                        + (f"  \n_assumption: {it['assumption']}_" if it.get("assumption") else ""))
            key = f"rv_{it['mail_id']}_{it['field']}"
            if c2.button("Confirm", key=key + "_ok"):
                analysis.set_review(it["mail_id"], it["field"], "confirm")
                st.rerun()
            new = c2.text_input("Correct to", key=key + "_v", label_visibility="collapsed",
                                placeholder="Correct to…")
            if new and c2.button("Save correction", key=key + "_fix"):
                analysis.set_review(it["mail_id"], it["field"], "correct", new)
                st.rerun()


def _export(df: pd.DataFrame, result: dict) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="Comparison", index=False)
        pd.DataFrame(result["ledger"]).to_excel(xw, sheet_name="Assumptions ledger", index=False)
        pd.DataFrame([{"FX": k, "INR per unit": v, "As of": FX_AS_OF} for k, v in FX_RATES.items()]).to_excel(
            xw, sheet_name="FX", index=False)
    return buf.getvalue()


def render() -> None:
    st.header("RFQ Standardiser & Comparison")
    st.caption("The model reads each response — any format — and says what it states, with a source and a "
               "confidence for every value. Plain Python then does all the arithmetic: FX, unit basis, credit days, "
               "freight, delivery vs need-by, certification verdicts. Every conversion is written to the ledger.")
    ensure_rfqs()
    rfqs = db.list_rfqs()
    with_resp = [r["rfq_id"] for r in rfqs if analysis.responses_for(r["rfq_id"])] or [r["rfq_id"] for r in rfqs]
    rid = st.selectbox("RFQ", with_resp, index=with_resp.index(DEMO_RFQ) if DEMO_RFQ in with_resp else 0,
                       format_func=lambda i: f"{i} · {next(r['title'] for r in rfqs if r['rfq_id'] == i)}")
    rfq = db.get_rfq(rid)
    _, _, _, po = current_plan()
    po = date.fromisoformat(rfq["planned_po_date"]) if rfq.get("planned_po_date") else po

    st.markdown("**1 · Choose the vendor responses to analyse**")
    chosen = _pick_responses(rid)
    if not chosen:
        return
    result = analysis.build(rfq, chosen, po)
    single = len({l["material"] for l in rfq["lines"]}) == 1

    st.markdown("**2 · Comparison**")
    fx = " · ".join(f"1 {k} = {v:.2f} INR" for k, v in FX_RATES.items() if k != "INR")
    st.caption(f"FX: {fx} — as on {FX_AS_OF}, {FX_SOURCE}. Planned PO {po:%d %b %Y}; delivery dates = PO + "
               "quoted lead time.")
    c = st.columns(4)
    c[0].metric("Responses analysed", len(result["docs"]))
    c[1].metric("Quotes compared", sum(1 for r in result["rows"] if r["quoted"]))
    c[2].metric("Needs review", len(result["queue"]))
    c[3].metric("Buyer decisions", len(result["flags"]))
    _flags(result)

    t1, t2, t3 = st.tabs(["Comparison", f"Review queue ({len(result['queue'])})",
                          f"Assumptions ledger ({len(result['ledger'])})"])
    with t1:
        materials = list(dict.fromkeys(l["material"] for l in rfq["lines"]))
        if single:
            st.markdown(f"This RFQ is for **{materials[0]}** — "
                        + ", ".join(f"{l['variant']} × {l['qty']}" if l["variant"] != "—" else f"{l['qty']} {l['uom']}"
                                    for l in rfq["lines"]) + ". Recommendation is per variant.")
            material = None
        else:
            pickm = st.selectbox("Raw material", ["All materials"] + materials,
                                 help="Filter the comparison to one raw material. Recommendation is per material.")
            material = None if pickm == "All materials" else pickm
        df, styled = _matrix(result, rfq, single, material)
        st.caption(LEGEND + "  Recommendation is ranked in plain Python: exclude vendors that didn't quote, are "
                   "late against need-by, fail a required standard or have no stock; then prefer full quantity, "
                   "fewest open issues, lowest total cost.")
        sel = st.dataframe(styled, hide_index=True, width="stretch", on_select="rerun",
                           selection_mode="single-row", key="matrix", height=min(40 + 35 * len(df), 720),
                           column_config={"Recommendation": st.column_config.TextColumn(width="medium"),
                                          "Item": st.column_config.TextColumn(width="medium")})
        st.download_button("Export comparison (xlsx)", _export(df, result), file_name=f"{rid}_comparison.xlsx")
        if sel.selection.rows:
            _trace(st.session_state["_matrix_rows"][sel.selection.rows[0]], result)
        else:
            st.info("Select any row to trace every number back to the cell, page, paragraph or photo region it "
                    "came from.", icon="🔎")
    with t2:
        _review(result)
    with t3:
        led = pd.DataFrame(result["ledger"])
        if not led.empty:
            vsel = st.multiselect("Vendor", sorted(led["vendor"].unique()), default=sorted(led["vendor"].unique()))
            st.dataframe(led[led["vendor"].isin(vsel)], hide_index=True, width="stretch",
                         column_config={"text": st.column_config.TextColumn("Entry", width="large")})
