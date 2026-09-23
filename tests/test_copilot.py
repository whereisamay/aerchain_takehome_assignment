import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import copilot  # noqa: E402
from demand import compute_demand, po_date_for  # noqa: E402
from rfq import validate_rfq  # noqa: E402

TARGET = date(2027, 2, 10)


def test_copilot_assembles_model_output_into_valid_rfq(monkeypatch):
    seen = {}

    def fake_json_call(system, messages, schema, **kw):
        seen["system"], seen["schema"] = system, schema
        return {"reply": "Drafted.", "material_code": "MAT-005",
                "variants": [{"variant": "Heavy-duty", "qty": 27, "need_by": "2026-11-18"}],
                "quality_standard": "ISO 15243 + supplier ISO 9001", "close_date": "2026-09-30",
                "notes": "", "assumptions": ["24 + ~10% spares = 27"], "open_questions": []}

    monkeypatch.setattr(copilot, "json_call", fake_json_call)
    demand = compute_demand({"PS-200": 12, "PS-075": 20}, TARGET)
    rfq, _ = copilot.draft_rfq([{"role": "user", "content": "heavy duty bearings +10% spares"}],
                               demand, po_date_for(TARGET), today=date(2026, 9, 23))
    assert rfq["rfq_id"] == "RFQ-2026-MCH-005"
    assert rfq["variants"][0]["uom"] == "nos"
    assert validate_rfq(rfq) == []
    # The model is given master data and the live demand plan, not canned answers
    assert "Heavy-duty" in seen["system"] and "PS-200 × 12 @ 2/unit" in seen["system"]
    assert seen["schema"]["properties"]["material_code"]["enum"][0] == "MAT-001"
