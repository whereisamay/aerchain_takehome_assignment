"""RFQ objects. JSON first, rendered to a document second (see rfq_xlsx.py).

An RFQ can cover several materials (e.g. everything needed for phase P1). Each line is one
material + variant. IDs: a single-material RFQ takes the material number (RFQ-2026-MCH-005 is
the bearing assembly); a multi-material RFQ takes the next free number from 101 up.
"""
from datetime import date, timedelta

import pandas as pd

from master_data import BUYER, MATERIALS, PHASES

RESPONSE_FIELDS = ["unit_price", "currency", "availability", "delivery_days", "credit_terms", "quality_cert"]

# Stated bases the vendor responses are normalised against later.
STANDARD_TERMS = {
    "price_basis": "Per single unit of the stated UoM (e.g. one bearing assembly, not a set or pair)",
    "currency": "INR preferred. Other currencies accepted if stated explicitly per line",
    "freight": "Please state whether the price is delivered to our plant (freight included) or ex-works",
    "delivery": "Delivery time in calendar days from PO date to receipt at our plant",
    "credit_terms": "Credit period in days from invoice date. State any early-payment discount separately",
    "availability": "Full quantity, partial quantity (state how many), or not available — per line",
    "quality": "Standard certified to, certificate number, issuing body and expiry date. Attach copies",
    "validity": "Quote valid for at least 60 days from close date",
}

MULTI_START = 101
# Cap per RFQ: each item adds to what the model must read and return per vendor response.
MAX_ITEMS = 8


def default_close_date(po_date: date, today: date | None = None) -> date:
    """Quotes close 5 days before the planned PO, but never less than 3 days from today."""
    today = today or date.today()
    return max(po_date - timedelta(days=5), today + timedelta(days=3))


def rfq_id_for(material_codes: list[str], year: int, taken: set[str]) -> str:
    codes = sorted(set(material_codes))
    if len(codes) == 1:
        return f"RFQ-{year}-MCH-{MATERIALS[codes[0]].no:03d}"
    n = MULTI_START
    while f"RFQ-{year}-MCH-{n:03d}" in taken:
        n += 1
    return f"RFQ-{year}-MCH-{n:03d}"


def title_for(lines: list[dict]) -> str:
    codes = list(dict.fromkeys(l["material_code"] for l in lines))
    if len(codes) == 1:
        return MATERIALS[codes[0]].name
    phases = sorted({MATERIALS[c].phase for c in codes})
    if len(phases) == 1:
        return f"{phases[0]} · {PHASES[phases[0]].name} — {len(codes)} materials"
    return f"{len(codes)} materials across {', '.join(phases)}"


def line(material_code: str, variant: str, qty: int, need_by: str) -> dict:
    m = MATERIALS[material_code]
    return {"material_code": m.code, "material": m.name, "phase": m.phase, "variant": variant,
            "qty": int(qty), "uom": m.uom, "need_by": need_by, "quality_standard": m.quality_standard}


def lines_from_demand(demand: pd.DataFrame, material_codes: list[str]) -> list[dict]:
    rows = demand[demand["Material code"].isin(material_codes)].sort_values(["No"])
    return [line(r["Material code"], r["Variant"], int(r["Total qty"]),
                 pd.Timestamp(r["Need-by"]).date().isoformat())
            for _, r in rows.iterrows() if int(r["Total qty"]) > 0]


def assemble(lines: list[dict], close: date, po_date: date | None, taken: set[str], notes: str = "",
             rfq_id: str | None = None) -> dict:
    return {
        "rfq_id": rfq_id or rfq_id_for([l["material_code"] for l in lines], close.year, taken),
        "title": title_for(lines),
        "lines": lines,
        "buyer": dict(BUYER),
        "terms": dict(STANDARD_TERMS),
        "response_fields": list(RESPONSE_FIELDS),
        "planned_po_date": po_date.isoformat() if po_date else "",
        "close_date": close.isoformat(),
        "notes": notes,
    }


def build_rfq(demand: pd.DataFrame, material_codes: list[str], po_date: date, taken: set[str],
              today: date | None = None) -> dict:
    return assemble(lines_from_demand(demand, material_codes), default_close_date(po_date, today), po_date, taken)


def materials_in(rfq: dict) -> list[str]:
    return list(dict.fromkeys(l["material_code"] for l in rfq["lines"]))


def standards_in(rfq: dict) -> list[tuple[str, str]]:
    """(material name, quality standard) per distinct material, in line order."""
    seen = {}
    for l in rfq["lines"]:
        seen.setdefault(l["material"], l["quality_standard"])
    return list(seen.items())


def validate_rfq(rfq: dict) -> list[str]:
    """Problems a buyer must fix before the RFQ can be exported or sent. Empty list = ok."""
    problems = []
    if not rfq.get("lines"):
        return ["No items"]
    if len(rfq["lines"]) > MAX_ITEMS:
        problems.append(f"{len(rfq['lines'])} items — an RFQ can hold at most {MAX_ITEMS}. Split it into two RFQs.")
    for i, l in enumerate(rfq["lines"], 1):
        m = MATERIALS.get(l.get("material_code", ""))
        if not m:
            problems.append(f"Item {i}: unknown material {l.get('material_code')!r}")
            continue
        if l.get("variant") not in m.variants:
            problems.append(f"Item {i}: {l.get('variant')!r} is not a variant of {m.name} ({', '.join(m.variants)})")
        if not isinstance(l.get("qty"), int) or l["qty"] <= 0:
            problems.append(f"Item {i}: quantity must be a positive whole number")
        try:
            date.fromisoformat(l.get("need_by", ""))
        except (TypeError, ValueError):
            problems.append(f"Item {i}: need-by date is not a valid date")
    keys = [(l.get("material_code"), l.get("variant")) for l in rfq["lines"]]
    if len(keys) != len(set(keys)):
        problems.append("The same material and variant appears twice")
    try:
        date.fromisoformat(rfq.get("close_date", ""))
    except (TypeError, ValueError):
        problems.append("Close date is not a valid date")
    return problems
