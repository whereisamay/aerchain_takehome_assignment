import json
from datetime import date

import pandas as pd
import streamlit as st

import db
from copilot import assistant_turn_text, draft_rfq
from llm import LLMError, model_name
from master_data import MATERIALS, PHASES
from rfq import build_rfq, line, materials_in, title_for, validate_rfq
from rfq_xlsx import render_rfq_xlsx
from state import current_plan, ensure_rfqs

ALL_VARIANTS = sorted({v for m in MATERIALS.values() for v in m.variants})
MAT_LABEL = {m.code: f"{m.phase} · {m.name}" for m in MATERIALS.values()}
MAT_BY_LABEL = {v: k for k, v in MAT_LABEL.items()}


def _taken() -> set[str]:
    return {r["rfq_id"] for r in db.list_rfqs()}


def _select(codes: list[str]) -> None:
    st.session_state["gen_mats"] = codes


def _create(rfq: dict) -> None:
    db.save_rfq(rfq, "generated")
    st.session_state["edit_pick"] = rfq["rfq_id"]
    st.session_state["gen_mats"] = []
    st.session_state["rfq_toast"] = f"Created {rfq['rfq_id']} · {rfq['title']}"


def _builder() -> None:
    _, _, demand, po = current_plan()
    demanded = [c for c in MATERIALS if c in set(demand["Material code"])]
    st.markdown("**Build from the demand plan** — pick the parts to put on one RFQ.")
    cols = st.columns(len(PHASES) + 1)
    for col, p in zip(cols, PHASES.values()):
        codes = [c for c in demanded if MATERIALS[c].phase == p.code]
        col.button(f"All {p.code} ({len(codes)})", on_click=_select, args=(codes,), use_container_width=True,
                   help=p.name, disabled=not codes)
    cols[-1].button("Clear", on_click=_select, args=([],), use_container_width=True)
    codes = st.multiselect("Materials on this RFQ", demanded, key="gen_mats", format_func=MAT_LABEL.get,
                           placeholder="Choose one or more materials, or use the phase buttons")
    if not codes:
        return
    preview = build_rfq(demand, codes, po, _taken())
    st.dataframe(pd.DataFrame(preview["lines"])[["material", "variant", "qty", "uom", "need_by", "phase"]],
                 hide_index=True, width="stretch")
    st.caption(f"Will be created as **{preview['rfq_id']}** · {preview['title']} · closes {preview['close_date']}")
    st.button("Create RFQ", type="primary", on_click=_create, args=(preview,))


def _copilot() -> None:
    st.caption(f"Describe what you need in plain English. The model ({model_name()}) drafts the RFQ JSON — "
               "which materials, variants, quantities and dates — and Python validates it. You review and edit "
               "the draft below before anything is saved or sent.")
    hist = st.session_state.setdefault("copilot_history", [])
    for turn in st.session_state.setdefault("copilot_display", []):
        with st.chat_message(turn["role"]):
            st.markdown(turn["text"])
    prompt = st.chat_input("e.g. Everything for P2, but add 10% spares on heavy-duty bearings")
    if st.button("Clear conversation"):
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
            rfq, out = draft_rfq(hist, demand, po, _taken())
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
    text += f"\n\n_Draft **{rfq['rfq_id']}** loaded into the editor below — save it to keep it._"
    st.session_state["copilot_display"].append({"role": "assistant", "text": text})
    st.session_state["draft"] = rfq
    st.session_state["edit_pick"] = "__draft__"
    st.rerun()


def _editor() -> None:
    rfqs = db.list_rfqs()
    draft = st.session_state.get("draft")
    label = {r["rfq_id"]: f"{r['rfq_id']} · {r['title']}" for r in rfqs}
    options = list(label)
    if draft:
        options.insert(0, "__draft__")
        label["__draft__"] = f"Co-pilot draft · {draft['rfq_id']} · {draft['title']} (unsaved)"
    if not options:
        return
    pick = st.session_state.get("edit_pick")
    pick = st.selectbox("Open RFQ", options, format_func=label.get,
                        index=options.index(pick) if pick in options else 0)
    st.session_state["edit_pick"] = pick
    rfq = draft if pick == "__draft__" else db.get_rfq(pick)

    if pick == "__draft__" and rfq.get("copilot"):
        with st.expander("Co-pilot assumptions and open questions", expanded=True):
            for a in rfq["copilot"]["assumptions"]:
                st.markdown(f"- {a}")
            for q in rfq["copilot"]["open_questions"]:
                st.markdown(f"- ❓ {q}")

    with st.form(f"ed_{pick}_{rfq['rfq_id']}"):
        c1, c2 = st.columns(2)
        close = c1.date_input("Quotes close", value=date.fromisoformat(rfq["close_date"]))
        c2.text_input("RFQ", value=f"{rfq['rfq_id']} · {rfq['title']}", disabled=True)
        df = pd.DataFrame(rfq["lines"])
        df["material"] = df["material_code"].map(MAT_LABEL)
        df["need_by"] = pd.to_datetime(df["need_by"]).dt.date
        edited = st.data_editor(
            df[["material", "variant", "qty", "uom", "need_by", "quality_standard"]], num_rows="dynamic",
            hide_index=True, width="stretch",
            column_config={
                "material": st.column_config.SelectboxColumn("Material", options=list(MAT_BY_LABEL),
                                                             required=True, width="medium"),
                "variant": st.column_config.SelectboxColumn("Variant", options=ALL_VARIANTS, required=True,
                                                            help="Materials without variants use —"),
                "qty": st.column_config.NumberColumn("Qty", min_value=1, step=1, required=True),
                "uom": st.column_config.TextColumn("UoM", disabled=True),
                "need_by": st.column_config.DateColumn("Need-by", required=True),
                "quality_standard": st.column_config.TextColumn("Quality standard", disabled=True),
            })
        notes = st.text_area("Notes to vendors", value=rfq.get("notes", ""))
        with st.expander("Response basis (terms sent to vendors)"):
            terms = {k: st.text_input(k.replace("_", " ").capitalize(), value=v) for k, v in rfq["terms"].items()}
        saved = st.form_submit_button("Save RFQ", type="primary")

    lines = []
    for _, r in edited.iterrows():
        code = MAT_BY_LABEL.get(r["material"])
        if not code:
            continue
        lines.append(line(code, r["variant"] or "—", int(r["qty"]) if pd.notna(r["qty"]) else 0,
                          r["need_by"].isoformat() if pd.notna(r["need_by"]) else ""))
    new = {k: v for k, v in rfq.items() if not k.startswith("_")}
    new.update({"close_date": close.isoformat(), "notes": notes, "terms": terms, "lines": lines})
    if lines:
        new["title"] = title_for(lines)
    problems = validate_rfq(new)
    if saved:
        if problems:
            st.error("Not saved:\n\n" + "\n".join(f"- {p}" for p in problems))
        else:
            db.save_rfq(new, "copilot" if pick == "__draft__" else "edited")
            if pick == "__draft__":
                st.session_state.pop("draft", None)
                st.session_state["edit_pick"] = new["rfq_id"]
            st.toast(f"Saved {new['rfq_id']}")
            st.rerun()

    c1, c2, _ = st.columns([1, 1, 3])
    if problems:
        st.warning("Fix before export:\n\n" + "\n".join(f"- {p}" for p in problems))
    else:
        c1.download_button("Export xlsx pack", render_rfq_xlsx(new), file_name=f"{new['rfq_id']}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if pick == "__draft__":
        if c2.button("Discard draft"):
            st.session_state.pop("draft", None)
            st.rerun()
    elif c2.button("Delete RFQ"):
        db.delete_rfq(pick)
        st.session_state.pop("edit_pick", None)
        st.rerun()
    with st.expander("RFQ JSON"):
        st.code(json.dumps(new, indent=2), language="json")


def render() -> None:
    st.header("RFQ Generator")
    st.caption("Put one or many parts on an RFQ — e.g. everything needed for phase P1. JSON first, rendered to "
               "an xlsx pack second. Next step: send it from the RFQ Sender.")
    ensure_rfqs()
    if msg := st.session_state.pop("rfq_toast", None):
        st.toast(msg)
    with st.container(border=True):
        _builder()
    with st.expander("…or describe it to the AI co-pilot", expanded=bool(st.session_state.get("copilot_display"))):
        _copilot()

    st.subheader("Your RFQs")
    rfqs = db.list_rfqs()
    st.dataframe(pd.DataFrame([{
        "RFQ": r["rfq_id"], "Scope": r["title"], "Lines": len(r["lines"]),
        "Materials": ", ".join(MATERIALS[c].name for c in materials_in(r)),
        "Earliest need-by": min(l["need_by"] for l in r["lines"]), "Closes": r["close_date"], "Origin": r["_origin"],
    } for r in rfqs]), hide_index=True, width="stretch")
    _editor()
