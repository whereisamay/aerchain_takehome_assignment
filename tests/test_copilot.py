import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import copilot  # noqa: E402
from demand import compute_demand, po_date_for  # noqa: E402
from rfq import validate_rfq  # noqa: E402

TARGET = date(2027, 2, 10)


def test_copilot_assembles_multi_material_model_output(monkeypatch):
    seen = {}

    def fake_json_call(system, messages, schema, **kw):
        seen["system"], seen["schema"] = system, schema
        return {"reply": "Drafted.",
                "lines": [{"material_code": "MAT-005", "variant": "Heavy-duty", "qty": 27, "need_by": "2026-11-18"},
                          {"material_code": "MAT-006", "variant": "Double cartridge", "qty": 12,
                           "need_by": "2026-11-18"}],
                "close_date": "2026-09-30", "notes": "", "assumptions": ["24 + ~10% spares = 27"],
                "open_questions": []}

    monkeypatch.setattr(copilot, "json_call", fake_json_call)
    demand = compute_demand({"PS-200": 12, "PS-075": 20}, TARGET)
    rfq, _ = copilot.draft_rfq([{"role": "user", "content": "P2 heavy stuff"}], demand, po_date_for(TARGET),
                               taken=set(), today=date(2026, 9, 23))
    assert rfq["rfq_id"] == "RFQ-2026-MCH-101"
    assert rfq["lines"][0]["uom"] == "nos" and rfq["lines"][1]["quality_standard"] == "API 682 4th ed."
    assert rfq["title"] == "P2 · Rotating assembly — 2 materials"
    assert validate_rfq(rfq) == []
    # The model is given master data and the live demand plan, not canned answers
    assert "Heavy-duty" in seen["system"] and "PS-200 × 12 @ 2/unit" in seen["system"]
    assert seen["schema"]["properties"]["lines"]["items"]["properties"]["material_code"]["enum"][0] == "MAT-001"
