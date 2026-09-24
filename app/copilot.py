"""RFQ co-pilot: buyer describes what they want in plain English, the model writes the RFQ JSON.

The model decides *what* the RFQ says (which materials, variants, quantities, dates). Python
assembles it (id, buyer block, terms, UoM, standards) and validates it against master data
before it can be exported.
"""
import json
from datetime import date

import pandas as pd

from llm import json_call
from master_data import BUYER, MATERIALS, PHASES
from rfq import assemble, line, validate_rfq

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "Short message back to the buyer: what you drafted and why."},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "material_code": {"type": "string", "enum": sorted(MATERIALS)},
                    "variant": {"type": "string"},
                    "qty": {"type": "integer"},
                    "need_by": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                },
                "required": ["material_code", "variant", "qty", "need_by"],
                "additionalProperties": False,
            },
        },
        "close_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "notes": {"type": "string", "description": "Extra requirements for vendors, or empty string."},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reply", "lines", "close_date", "notes",
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
The buyer describes what they want to source. You draft ONE request for quotation (RFQ). An RFQ may cover one
material or several (e.g. "everything for phase P1"); each line is one material + variant.

Today is {today.isoformat()}. Planned PO date from the current build plan: {po_date.isoformat() if po_date else 'unknown'}.

Material master (the only materials and variants that exist — use these exact codes and variant names; materials without variants use the variant name "—"):
{_master_data_text()}

Current demand plan (quantities and need-by dates calculated from the machine build plan):
{demand_txt}

Rules:
- Default quantities and need-by dates to the demand plan. Only deviate when the buyer explicitly asks, and record the deviation in assumptions.
- Quantities you derive from what the buyer says (e.g. "bearings for 5 more PS-200s") must be worked out from the demand plan's per-machine derivation, and the working shown in assumptions.
- Quality standards come from the material master automatically; put any extra requirement the buyer states in notes.
- Default close date: 5 days before the planned PO date, and never earlier than 3 days from today.
- If the request is ambiguous (which materials? which variants?) make the most reasonable draft and put the question in open_questions. Never invent a material or variant that is not in the master.
- Omit lines with zero quantity.
- If the buyer is refining an earlier draft, return the full updated RFQ, not just the change.
- notes: only vendor-facing requirements the buyer actually stated (packaging, inspection, delivery split, etc.).
- reply: two or three sentences, plain English."""


def draft_rfq(history: list[dict], demand: pd.DataFrame | None, po_date: date | None,
              taken: set[str] = frozenset(), today: date | None = None) -> tuple[dict, dict]:
    """history: [{"role": "user"|"assistant", "content": str}, ...] ending with the buyer's latest message.

    Returns (rfq, raw_model_output)."""
    today = today or date.today()
    out = json_call(_system(demand, po_date, today), history, SCHEMA, effort="medium")
    try:
        close = date.fromisoformat(out["close_date"])
    except ValueError:
        close = today
    lines = [line(l["material_code"], l["variant"], l["qty"], l["need_by"]) for l in out["lines"]]
    rfq = assemble(lines, close, po_date, set(taken), notes=out["notes"])
    rfq["close_date"] = out["close_date"]
    rfq["copilot"] = {"assumptions": out["assumptions"], "open_questions": out["open_questions"]}
    return rfq, out


def assistant_turn_text(out: dict) -> str:
    """Compact record of the model's last draft, fed back as the assistant turn for follow-ups."""
    return json.dumps({k: out[k] for k in ("reply", "lines", "close_date", "notes")}, ensure_ascii=False)


__all__ = ["draft_rfq", "assistant_turn_text", "validate_rfq"]
