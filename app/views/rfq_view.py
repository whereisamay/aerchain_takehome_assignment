import json
from datetime import date

import pandas as pd
import streamlit as st

import db
from copilot import assistant_turn_text, draft_rfq
from llm import LLMError, model_name
from master_data import MATERIALS
from rfq import build_rfqs, validate_rfq
from rfq_xlsx import render_rfq_xlsx
from state import current_plan


def _ensure_generated(force: bool = False) -> None:
    if force or not db.list_rfqs():
        _, _, demand, po = current_plan()
        for r in build_rfqs(demand, po):
            db.save_rfq(r, "generated")


def _copilot() -> None:
    st.subheader("RFQ co-pilot")
    st.caption(f"Describe what you need in plain English. The model ({model_name()}) drafts the RFQ JSON; "
               "you review and edit it below before anything is exported. One model call per message.")
    hist = st.session_state.setdefault("copilot_history", [])
    for turn in st.session_state.setdefault("copilot_display", []):
        with st.chat_message(turn["role"]):
            st.markdown(turn["text"])
    prompt = st.chat_input("e.g. Bearings for the build plan, but add 10% spares on heavy-duty and close quotes on 30 Sep")
    c1, _ = st.columns([1, 5])
    if c1.button("Clear conversation"):
        st.session_state["copilot_history"] = []
        st.session_state["copilot_display"] = []
        st.rerun()
    if not prompt:
        return
    _, _, demand, po = current_plan()
    hist.append({"role": "user", "content": prompt})
    st.session_state["copilot_display"].append({"role": "user", "text": prompt})
    with st.spinner("Drafting RFQ…"):
        try:
            rfq, out = draft_rfq(hist, demand, po)
        except LLMError as e:
            hist.pop()
            st.session_state["copilot_display"].pop()
            st.error(str(e))
            return
    hist.append({"role": "assistant", "content": assistant_turn_text(out)})
    text = out["reply"]
    if out["assumptions"]:
        text += "\n\n**Assumptions**\n" + "\n".join(f"- {a}" for a in out["assumptions"])
    if out["open_questions"]:
        text += "\n\n**Questions for you**\n" + "\n".join(f"- {q}" for q in out["open_questions"])
    text += f"\n\n_Draft **{rfq['rfq_id']}** loaded into the editor below._"
    st.session_state["copilot_display"].append({"role": "assistant", "text": text})
    st.session_state["editing"] = rfq
    st.session_state["editing_origin"] = "copilot"
    st.rerun()


def _editor() -> None:
    rfqs = db.list_rfqs()
    ids = [r["rfq_id"] for r in rfqs]
    draft = st.session_state.get("editing")

    st.subheader("Edit and export")
    options = ids.copy()
    label = {r["rfq_id"]: f"{r['rfq_id']} · {r['material']}" for r in rfqs}
    if draft and st.session_state.get("editing_origin") == "copilot":
        options = ["__draft__"] + options
        label["__draft__"] = f"Co-pilot draft · {draft['rfq_id']} · {draft['material']} (unsaved)"
    pick = st.selectbox("RFQ", options, format_func=lambda k: label[k])
    if pick == "__draft__":
        rfq, origin = draft, "copilot"
    else:
        rfq, origin = db.get_rfq(pick), "edited"

    m = MATERIALS[rfq["material_code"]]
    st.caption(f"{m.name} · phase {rfq['phase']} · variants available: {', '.join(m.variants)}")
    if rfq.get("copilot") and pick == "__draft__":
        with st.expander("Co-pilot assumptions and open questions", expanded=True):
            for a in rfq["copilot"]["assumptions"]:
                st.markdown(f"- {a}")
            for q in rfq["copilot"]["open_questions"]:
                st.markdown(f"- ❓ {q}")

    key = f"ed_{pick}_{rfq['rfq_id']}"
    with st.form(key):
        c1, c2 = st.columns(2)
        close = c1.date_input("Quotes close", value=date.fromisoformat(rfq["close_date"]))
        qs = c2.text_input("Quality standard required", value=rfq["quality_standard"])
        lines = pd.DataFrame(rfq["variants"])
        lines["need_by"] = pd.to_datetime(lines["need_by"]).dt.date
        edited = st.data_editor(
            lines[["variant", "qty", "uom", "need_by"]], num_rows="dynamic", hide_index=True, width="stretch",
            column_config={
                "variant": st.column_config.SelectboxColumn("Variant", options=list(m.variants), required=True),
                "qty": st.column_config.NumberColumn("Qty", min_value=1, step=1, required=True),
                "uom": st.column_config.TextColumn("UoM", disabled=True, default=m.uom),
                "need_by": st.column_config.DateColumn("Need-by", required=True),
            })
        notes = st.text_area("Notes to vendors", value=rfq.get("notes", ""))
        with st.expander("Response basis (terms sent to vendors)"):
            terms = {k: st.text_input(k.replace("_", " ").capitalize(), value=v) for k, v in rfq["terms"].items()}
        saved = st.form_submit_button("Save RFQ", type="primary")

    new = {**rfq, "close_date": close.isoformat(), "quality_standard": qs, "notes": notes, "terms": terms,
           "variants": [{"variant": r["variant"], "qty": int(r["qty"]) if pd.notna(r["qty"]) else 0,
                         "uom": m.uom, "need_by": r["need_by"].isoformat() if pd.notna(r["need_by"]) else ""}
                        for _, r in edited.iterrows()]}
    new.pop("_origin", None)
    new.pop("_updated_at", None)
    problems = validate_rfq(new)
    if saved:
        if problems:
            st.error("Not saved:\n\n" + "\n".join(f"- {p}" for p in problems))
        else:
            db.save_rfq(new, origin)
            if pick == "__draft__":
                st.session_state.pop("editing", None)
            st.success(f"Saved {new['rfq_id']}.")
            st.rerun()

    if problems:
        st.warning("Fix before export:\n\n" + "\n".join(f"- {p}" for p in problems))
    else:
        st.download_button("Export xlsx pack", render_rfq_xlsx(new), file_name=f"{new['rfq_id']}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with st.expander("RFQ JSON"):
        st.code(json.dumps(new, indent=2), language="json")


def render() -> None:
    st.header("RFQs")
    st.caption("One RFQ per material, generated deterministically from the demand plan. "
               "JSON first, rendered to an xlsx pack second.")
    _ensure_generated()
    rfqs = db.list_rfqs()
    st.dataframe(pd.DataFrame([{
        "RFQ": r["rfq_id"], "Material": r["material"], "Phase": r["phase"],
        "Lines": ", ".join(f"{v['variant']} × {v['qty']}" if v["variant"] != "—" else f"{v['qty']} {v['uom']}"
                           for v in r["variants"]),
        "Need-by": min(v["need_by"] for v in r["variants"]), "Closes": r["close_date"], "Origin": r["_origin"],
    } for r in rfqs]), hide_index=True, width="stretch")
    if st.button("Regenerate all from current demand plan",
                 help="Overwrites every RFQ, including edits, with values from the Demand screen."):
        _ensure_generated(force=True)
        st.rerun()

    with st.container(border=True):
        _copilot()
    _editor()
