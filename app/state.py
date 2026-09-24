"""Session-level plan state shared between screens."""
from datetime import date

import streamlit as st

from demand import compute_demand, po_date_for

DEFAULT_MACHINE_QTY = {"PS-200": 12, "PS-075": 20}
DEFAULT_TARGET = date(2027, 2, 10)


def current_plan():
    """(machine_qty, target_completion, demand_df, planned_po_date) for the plan on the Demand screen.

    Need-by dates come from the target completion and the phase schedule. The planned PO date is the
    buyer's call: it defaults to the build start but can be set to anything on the Demand Planner."""
    qty = st.session_state.get("machine_qty", DEFAULT_MACHINE_QTY)
    target = st.session_state.get("target_completion", DEFAULT_TARGET)
    po = st.session_state.get("planned_po") or po_date_for(target)
    return qty, target, compute_demand(qty, target), po


DEMO_RFQ = "RFQ-2026-MCH-005"


def ensure_rfqs() -> None:
    """Drop RFQs saved in the old single-material format. Nothing is auto-created: RFQs come from the
    RFQ Generator. (The demo vendor replies answer RFQ-2026-MCH-005, the bearing-only RFQ.)"""
    import db
    db.purge_legacy_rfqs()
