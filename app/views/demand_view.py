from datetime import date

import pandas as pd
import streamlit as st

from demand import compute_demand, po_date_for
from master_data import BOM, MACHINES, MATERIALS, PHASES
from state import DEFAULT_MACHINE_QTY, DEFAULT_TARGET


def _reset() -> None:
    for m in MACHINES:
        st.session_state[f"dq_{m}"] = 0
    st.session_state["dq_target"] = DEFAULT_TARGET
    st.session_state["dq_po"] = None
    st.session_state["machine_qty"] = {m: 0 for m in MACHINES}
    st.session_state["target_completion"] = DEFAULT_TARGET
    st.session_state["planned_po"] = None


def render() -> None:
    st.header("Demand Planner")
    st.caption("Machine build plan → material demand by phase, with need-by dates. Deterministic, no AI.")

    saved = st.session_state.get("machine_qty", DEFAULT_MACHINE_QTY)
    for m in MACHINES:
        st.session_state.setdefault(f"dq_{m}", saved.get(m, 0))
    st.session_state.setdefault("dq_target", st.session_state.get("target_completion", DEFAULT_TARGET))
    st.session_state.setdefault("dq_po", st.session_state.get("planned_po"))

    with st.form("demand_form"):
        cols = st.columns(len(MACHINES) + 2)
        machine_qty = {m.code: col.number_input(f"{m.code} — {m.name}", min_value=0, step=1, key=f"dq_{m.code}")
                       for col, m in zip(cols, MACHINES.values())}
        target = cols[-2].date_input("Target completion date", key="dq_target")
        planned = cols[-1].date_input("Planned PO date (optional)", key="dq_po",
                                      help="When you expect to place the orders. Vendor lead times are counted "
                                           "from this date. Leave blank to use the build start.")
        st.form_submit_button("Calculate demand", type="primary")
    st.button("Reset to zero", on_click=_reset)

    st.session_state["machine_qty"] = machine_qty
    st.session_state["target_completion"] = target
    st.session_state["planned_po"] = planned

    po = planned or po_date_for(target)
    df = compute_demand(machine_qty, target)
    st.caption(f"Orders assumed placed on **{po:%d %b %Y}**" + ("" if planned else " (build start — set a planned "
               "PO date above to change it)") + ". Need-by dates below come from the target completion date and "
               "the phase schedule; they are guidance, not hard limits.")
    if po < date.today():
        st.info(f"{po:%d %b %Y} is already in the past — consider setting a later planned PO date.")

    if df.empty:
        st.info("Enter at least one machine quantity.")
        return

    phase_labels = {p.code: f"{p.code} · {p.name} — needed by " for p in PHASES.values()}
    for phase, grp in df.groupby("Phase", sort=True):
        st.subheader(f"{phase_labels[phase]}{grp['Need-by'].iloc[0]:%d %b %Y}")
        st.dataframe(
            grp[["Material code", "Material", "Variant", "Total qty", "UoM", "Derivation", "Quality standard"]],
            hide_index=True, width="stretch")

    with st.expander("Master data: phases and per-machine BOM"):
        st.dataframe(pd.DataFrame([
            {"Phase": p.code, "Name": p.name, "Starts (weeks after PO)": p.start_week,
             "Duration (weeks)": p.duration_weeks} for p in PHASES.values()]), hide_index=True)
        st.dataframe(pd.DataFrame([
            {"Machine": mc, "Material": MATERIALS[mat].name, "Variant": v, "Qty per machine": q}
            for mc, lines in BOM.items() for mat, v, q in lines]), hide_index=True, width="stretch")
