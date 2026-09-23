"""RFQ objects. JSON first, rendered to a document second (see rfq_xlsx.py)."""
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


def rfq_id_for(material_no: int, year: int) -> str:
    return f"RFQ-{year}-MCH-{material_no:03d}"


def default_close_date(po_date: date, today: date | None = None) -> date:
    """Quotes close 5 days before the planned PO, but never less than 3 days from today."""
    today = today or date.today()
    return max(po_date - timedelta(days=5), today + timedelta(days=3))


def build_rfqs(demand: pd.DataFrame, po_date: date, today: date | None = None) -> list[dict]:
    close = default_close_date(po_date, today)
    rfqs = []
    for code, grp in demand.groupby("Material code", sort=True):
        m = MATERIALS[code]
        variants = [
            {"variant": r["Variant"], "qty": int(r["Total qty"]), "uom": r["UoM"],
             "need_by": pd.Timestamp(r["Need-by"]).date().isoformat()}
            for _, r in grp.iterrows() if int(r["Total qty"]) > 0
        ]
        rfqs.append({
            "rfq_id": rfq_id_for(m.no, close.year),
            "material_code": m.code,
            "material": m.name,
            "phase": m.phase,
            "phase_name": PHASES[m.phase].name,
            "variants": variants,
            "quality_standard": m.quality_standard,
            "spec_notes": m.spec_notes,
            "buyer": dict(BUYER),
            "terms": dict(STANDARD_TERMS),
            "response_fields": list(RESPONSE_FIELDS),
            "planned_po_date": po_date.isoformat(),
            "close_date": close.isoformat(),
            "notes": "",
        })
    return rfqs


def validate_rfq(rfq: dict) -> list[str]:
    """Problems a buyer must fix before the RFQ can be exported. Empty list = ok."""
    problems = []
    m = MATERIALS.get(rfq.get("material_code", ""))
    if not m:
        return [f"Unknown material code {rfq.get('material_code')!r}"]
    if not rfq.get("variants"):
        problems.append("No line items")
    for v in rfq.get("variants", []):
        if v.get("variant") not in m.variants:
            problems.append(f"Variant {v.get('variant')!r} is not a known variant of {m.name} ({', '.join(m.variants)})")
        if not isinstance(v.get("qty"), int) or v["qty"] <= 0:
            problems.append(f"Quantity for {v.get('variant')!r} must be a positive whole number")
        try:
            date.fromisoformat(v.get("need_by", ""))
        except (TypeError, ValueError):
            problems.append(f"Need-by date for {v.get('variant')!r} is not a valid date")
    try:
        date.fromisoformat(rfq.get("close_date", ""))
    except (TypeError, ValueError):
        problems.append("Close date is not a valid date")
    return problems
