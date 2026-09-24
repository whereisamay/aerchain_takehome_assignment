"""Session-level plan state shared between screens."""
from datetime import date

import streamlit as st

from demand import compute_demand, po_date_for

DEFAULT_MACHINE_QTY = {"PS-200": 12, "PS-075": 20}
DEFAULT_TARGET = date(2027, 2, 10)


def current_plan():
    """(machine_qty, target_completion, demand_df, po_date) for the plan on the Demand screen."""
    qty = st.session_state.get("machine_qty", DEFAULT_MACHINE_QTY)
    target = st.session_state.get("target_completion", DEFAULT_TARGET)
    return qty, target, compute_demand(qty, target), po_date_for(target)


DEMO_RFQ = "RFQ-2026-MCH-005"


def ensure_rfqs() -> None:
    """First run: seed the demo RFQ (bearing assembly, the one the vendor dataset answers)."""
    import db
    from rfq import build_rfq
    db.purge_legacy_rfqs()
    if not db.list_rfqs():
        _, _, demand, po = current_plan()
        db.save_rfq(build_rfq(demand, ["MAT-005"], po, set()), "demo seed")
