"""Demand generator: machine build quantities + target completion -> material demand with need-by dates."""
from datetime import date, timedelta

import pandas as pd

from master_data import BOM, BUILD_WEEKS, MATERIALS, PHASES


def po_date_for(target_completion: date) -> date:
    """Latest PO date that still lets the build finish on the target date."""
    return target_completion - timedelta(weeks=BUILD_WEEKS)


def need_by_date(po_date: date, phase: str) -> date:
    """Each material is required at the start of its phase."""
    return po_date + timedelta(weeks=PHASES[phase].start_week)


def compute_demand(machine_qty: dict[str, int], target_completion: date,
                   include_zero: bool = False) -> pd.DataFrame:
    po = po_date_for(target_completion)

    qty: dict[tuple[str, str], int] = {}
    used_by: dict[tuple[str, str], list[str]] = {}
    for machine, n in machine_qty.items():
        if n <= 0:
            continue
        for mat, variant, per_unit in BOM[machine]:
            key = (mat, variant)
            qty[key] = qty.get(key, 0) + per_unit * n
            used_by.setdefault(key, []).append(f"{machine} × {n} @ {per_unit}/unit")

    rows = []
    for m in MATERIALS.values():
        for variant in m.variants:
            q = qty.get((m.code, variant), 0)
            if q == 0 and not include_zero:
                continue
            rows.append({
                "No": m.no,
                "Material code": m.code,
                "Material": m.name,
                "Variant": variant,
                "Phase": m.phase,
                "Phase name": PHASES[m.phase].name,
                "Total qty": q,
                "UoM": m.uom,
                "Need-by": need_by_date(po, m.phase),
                "Derivation": "; ".join(used_by.get((m.code, variant), [])) or "not used by selected machines",
                "Quality standard": m.quality_standard,
            })
    return pd.DataFrame(rows)
