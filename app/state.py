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
