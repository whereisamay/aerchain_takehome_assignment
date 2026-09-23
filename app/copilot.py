"""RFQ co-pilot: buyer describes what they want in plain English, the model writes the RFQ JSON.

The model decides *what* the RFQ says. Python assembles it (ids, buyer block, terms, UoM)
and validates it against master data before it can be exported.
"""
import json
from datetime import date

import pandas as pd

from llm import json_call
from master_data import BUYER, MATERIALS, PHASES
from rfq import RESPONSE_FIELDS, STANDARD_TERMS, rfq_id_for, validate_rfq

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "Short message back to the buyer: what you drafted and why."},
        "material_code": {"type": "string", "enum": sorted(MATERIALS)},
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "variant": {"type": "string"},
                    "qty": {"type": "integer"},
                    "need_by": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                },
                "required": ["variant", "qty", "need_by"],
                "additionalProperties": False,
            },
        },
        "quality_standard": {"type": "string"},
        "close_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "notes": {"type": "string", "description": "Extra requirements for vendors, or empty string."},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reply", "material_code", "variants", "quality_standard", "close_date", "notes",
                 "assumptions", "open_questions"],
    "additionalProperties": False,
}


def _master_data_text() -> str:
    lines = []
    for m in MATERIALS.values():
        lines.append(f"- {m.code} | {m.name} | phase {m.phase} ({PHASES[m.phase].name}) | UoM {m.uom} | "
                     f"variants: {', '.join(m.variants)} | quality standard: {m.quality_standard}")
    return "\n".join(lines)


def _system(demand: pd.DataFrame | None, po_date: date | None, today: date) -> str:
    demand_txt = "No demand plan has been calculated yet."
    if demand is not None and not demand.empty:
        demand_txt = demand[["Material code", "Material", "Variant", "Total qty", "UoM", "Need-by", "Derivation"]] \
            .to_csv(index=False)
    return f"""You are the RFQ co-pilot for the procurement team at {BUYER['company']}, a manufacturer of industrial pump skids.
The buyer describes what they want to source. You draft ONE request for quotation (RFQ) for ONE material.

Today is {today.isoformat()}. Planned PO date from the current build plan: {po_date.isoformat() if po_date else 'unknown'}.

Material master (the only materials and variants that exist — use these exact codes and variant names; materials without variants use the variant name "—"):
{_master_data_text()}

Current demand plan (quantities and need-by dates calculated from the machine build plan):
{demand_txt}

Rules:
- Default quantities and need-by dates to the demand plan. Only deviate when the buyer explicitly asks, and record the deviation in assumptions.
- Quantities you derive from what the buyer says (e.g. "bearings for 5 more PS-200s") must be worked out from the demand plan's per-machine derivation, and the working shown in assumptions.
- Default the quality standard to the material master. Add to it only if the buyer asks.
- Default close date: 5 days before the planned PO date, and never earlier than 3 days from today.
- If the request is ambiguous (which material? which variants?) make the most reasonable draft and put the question in open_questions. Never invent a material or variant that is not in the master.
- If the buyer is refining an earlier draft, return the full updated RFQ, not just the change.
- notes: only vendor-facing requirements the buyer actually stated (packaging, inspection, delivery split, etc.).
- reply: two or three sentences, plain English."""


def draft_rfq(history: list[dict], demand: pd.DataFrame | None, po_date: date | None,
              today: date | None = None) -> tuple[dict, dict]:
    """history: [{"role": "user"|"assistant", "content": str}, ...] ending with the buyer's latest message.

    Returns (rfq, raw_model_output)."""
    today = today or date.today()
    out = json_call(_system(demand, po_date, today), history, SCHEMA, effort="medium")
    m = MATERIALS[out["material_code"]]
    try:
        year = date.fromisoformat(out["close_date"]).year
    except ValueError:
        year = today.year
    rfq = {
        "rfq_id": rfq_id_for(m.no, year),
        "material_code": m.code,
        "material": m.name,
        "phase": m.phase,
        "phase_name": PHASES[m.phase].name,
        "variants": [{**v, "uom": m.uom} for v in out["variants"]],
        "quality_standard": out["quality_standard"],
        "spec_notes": m.spec_notes,
        "buyer": dict(BUYER),
        "terms": dict(STANDARD_TERMS),
        "response_fields": list(RESPONSE_FIELDS),
        "planned_po_date": po_date.isoformat() if po_date else "",
        "close_date": out["close_date"],
        "notes": out["notes"],
        "copilot": {"assumptions": out["assumptions"], "open_questions": out["open_questions"]},
    }
    return rfq, out


def assistant_turn_text(out: dict) -> str:
    """Compact record of the model's last draft, fed back as the assistant turn for follow-ups."""
    return json.dumps({k: out[k] for k in ("reply", "material_code", "variants", "quality_standard",
                                           "close_date", "notes")}, ensure_ascii=False)


__all__ = ["draft_rfq", "assistant_turn_text", "validate_rfq"]
