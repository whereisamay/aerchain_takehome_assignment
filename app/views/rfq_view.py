import json
from datetime import date

import pandas as pd
import streamlit as st

import db
from copilot import assistant_turn_text, draft_rfq
from llm import LLMError, model_name
from master_data import MATERIALS, PHASES
from rfq import MAX_ITEMS, build_rfq, line, lines_from_demand, materials_in, title_for, validate_rfq
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
                 hide_index=True, width="stretch",
                 column_config={"material": "Material", "variant": "Variant", "qty": "Qty", "uom": "UoM",
                                "need_by": "Need-by", "phase": "Phase"})
    tight = sorted({l["material"] for l in preview["lines"]
                    if (date.fromisoformat(l["need_by"]) - po).days < 14})
    if tight:
        st.warning(f"Needed within 2 weeks of the planned PO date ({po:%d %b %Y}): {', '.join(tight)}. Most vendors "
                   "cannot deliver in that time — expect late quotes, or move the target completion date in the "
                   "Demand Planner.")
    too_many = len(preview["lines"]) > MAX_ITEMS
    if too_many:
        st.error(f"{len(preview['lines'])} items selected — an RFQ can hold at most {MAX_ITEMS}. Remove some "
                 "materials or split them across two RFQs.")
    else:
        st.caption(f"Will be created as **{preview['rfq_id']}** · {preview['title']} · {len(preview['lines'])} "
                   f"of max {MAX_ITEMS} items · closes {preview['close_date']}")
    st.button("Create RFQ", type="primary", on_click=_create, args=(preview,), disabled=too_many)


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


def _current() -> tuple[dict | None, str]:
    """The RFQ being worked on right now: an unsaved co-pilot draft, else the last one created or saved."""
    if st.session_state.get("draft"):
        return st.session_state["draft"], "__draft__"
    rid = st.session_state.get("edit_pick")
    rfq = db.get_rfq(rid) if rid else None
    return rfq, rid


def _editor() -> None:
    rfq, pick = _current()
    if not rfq:
        st.info("Build an RFQ above (or ask the co-pilot) and it will open here for review and export.")
        return
    st.subheader(f"{rfq['rfq_id']} · {rfq['title']}" + (" — co-pilot draft (unsaved)" if pick == "__draft__" else ""))

    if pick == "__draft__" and rfq.get("copilot"):
        with st.expander("Co-pilot assumptions and open questions", expanded=True):
            for a in rfq["copilot"]["assumptions"]:
                st.markdown(f"- {a}")
            for q in rfq["copilot"]["open_questions"]:
                st.markdown(f"- ❓ {q}")

    with st.form(f"ed_{pick}_{rfq['rfq_id']}_{st.session_state.get('refresh_n', 0)}"):
        close = st.date_input("Quotes close", value=date.fromisoformat(rfq["close_date"]))
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

    items = []
    for _, r in edited.iterrows():
        code = MAT_BY_LABEL.get(r["material"])
        if not code:
            continue
        items.append(line(code, r["variant"] or "—", int(r["qty"]) if pd.notna(r["qty"]) else 0,
                          r["need_by"].isoformat() if pd.notna(r["need_by"]) else ""))
    new = {k: v for k, v in rfq.items() if not k.startswith("_")}
    new.update({"close_date": close.isoformat(), "notes": notes, "terms": terms, "lines": items})
    if items:
        new["title"] = title_for(items)
    problems = validate_rfq(new)
    if saved:
        if problems:
            st.error("Not saved:\n\n" + "\n".join(f"- {p}" for p in problems))
        else:
            db.save_rfq(new, "copilot" if pick == "__draft__" else "edited")
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


def _refresh_demand() -> None:
    """Pull the latest plan from the Demand Planner into the RFQ being worked on."""
    rfq, pick = _current()
    st.session_state["refresh_n"] = st.session_state.get("refresh_n", 0) + 1
    if not rfq or pick == "__draft__":
        st.session_state["rfq_toast"] = "Demand refreshed from the Demand Planner"
        return
    _, _, demand, _ = current_plan()
    fresh = lines_from_demand(demand, materials_in(rfq))
    if not fresh:
        st.session_state["rfq_toast"] = "The current demand plan has no demand for these materials"
        return
    rfq = {**rfq, "lines": fresh, "title": title_for(fresh)}
    db.save_rfq(rfq, "edited")
    st.session_state["rfq_toast"] = f"{rfq['rfq_id']} updated with the latest quantities and need-by dates"


def _reset_rfqs() -> None:
    db.delete_all_rfqs()
    for k in ("draft", "edit_pick", "gen_mats", "copilot_history", "copilot_display"):
        st.session_state.pop(k, None)
    st.session_state["gen_mats"] = []
    st.session_state["rfq_toast"] = "All RFQs removed"


def render() -> None:
    st.header("RFQ Generator")
    st.caption(f"Put one or several parts on an RFQ (up to {MAX_ITEMS} items) — e.g. everything needed for phase "
               "P1. Built live from the Demand Planner. JSON first, rendered to an xlsx pack second. Next step: "
               "send it from the RFQ Sender.")
    ensure_rfqs()
    if msg := st.session_state.pop("rfq_toast", None):
        st.toast(msg)
    c1, c2, _ = st.columns([1, 1, 4])
    c1.button("Refresh demand", on_click=_refresh_demand, use_container_width=True,
              help="Re-read the Demand Planner and update the RFQ below with the latest quantities and dates.")
    c2.button("Reset RFQs", on_click=_reset_rfqs, use_container_width=True,
              help="Delete every RFQ created so far.")
    with st.container(border=True):
        _builder()
    with st.expander("…or describe it to the AI co-pilot", expanded=bool(st.session_state.get("copilot_display"))):
        _copilot()
    _editor()
