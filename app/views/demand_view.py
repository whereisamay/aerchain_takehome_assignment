from datetime import date

import pandas as pd
import streamlit as st

from demand import compute_demand, po_date_for
from master_data import BOM, BUILD_WEEKS, MACHINES, MATERIALS, PHASES


def render() -> None:
    st.header("Demand")
    st.caption("Machine build plan → material demand by phase, with need-by dates. Deterministic, no AI.")

    with st.form("demand_form"):
        cols = st.columns(len(MACHINES) + 1)
        machine_qty = {}
        for col, m in zip(cols, MACHINES.values()):
            machine_qty[m.code] = col.number_input(
                f"{m.code} — {m.name}", min_value=0, step=1,
                value=st.session_state.get("machine_qty", {}).get(m.code, {"PS-200": 12, "PS-075": 20}[m.code]))
        target = cols[-1].date_input("Target completion date",
                                     value=st.session_state.get("target_completion", date(2027, 2, 10)))
        include_zero = st.checkbox("Show variants with zero demand")
        st.form_submit_button("Calculate demand", type="primary")

    st.session_state["machine_qty"] = machine_qty
    st.session_state["target_completion"] = target

    po = po_date_for(target)
    df = compute_demand(machine_qty, target, include_zero=include_zero)
    st.session_state["demand"] = compute_demand(machine_qty, target)

    c1, c2, c3 = st.columns(3)
    c1.metric("Latest PO date", po.strftime("%d %b %Y"))
    c2.metric("Build duration", f"{BUILD_WEEKS} weeks")
    c3.metric("Demand lines", len(df))
    if po < date.today():
        st.warning(f"Latest PO date {po:%d %b %Y} is already in the past — the target completion is not achievable.")

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
