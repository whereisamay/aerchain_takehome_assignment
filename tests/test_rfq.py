import sys
from datetime import date
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from openpyxl import load_workbook  # noqa: E402

from demand import compute_demand, po_date_for  # noqa: E402
from rfq import build_rfqs, validate_rfq  # noqa: E402
from rfq_xlsx import render_rfq_xlsx  # noqa: E402

TARGET = date(2027, 2, 10)


def _rfqs():
    return build_rfqs(compute_demand({"PS-200": 12, "PS-075": 20}, TARGET), po_date_for(TARGET),
                      today=date(2026, 9, 23))


def test_all_ten_generate_and_validate():
    rfqs = _rfqs()
    assert len(rfqs) == 10
    assert all(validate_rfq(r) == [] for r in rfqs)
    bearing = next(r for r in rfqs if r["material_code"] == "MAT-005")
    assert bearing["rfq_id"] == "RFQ-2026-MCH-005"
    assert bearing["close_date"] == "2026-10-02"
    assert {v["variant"]: v["qty"] for v in bearing["variants"]} == {"Standard": 40, "Heavy-duty": 24, "High-speed": 20}
    assert all(v["need_by"] == "2026-11-18" for v in bearing["variants"])


def test_xlsx_pack_has_three_sheets_with_variant_rows():
    for rfq in _rfqs():
        wb = load_workbook(BytesIO(render_rfq_xlsx(rfq)))
        assert wb.sheetnames == ["Terms", "Line items", "Certifications"]
    bearing = next(r for r in _rfqs() if r["material_code"] == "MAT-005")
    ws = load_workbook(BytesIO(render_rfq_xlsx(bearing)))["Line items"]
    variants = [ws.cell(row=r, column=2).value for r in range(7, 10)]
    assert variants == ["Standard", "Heavy-duty", "High-speed"]
    assert ws.cell(row=7, column=7).fill.fgColor.rgb.endswith("FFF2CC")  # unit price is a shaded input


def test_validation_catches_bad_edits():
    rfq = _rfqs()[4]
    rfq["variants"][0]["variant"] = "Ceramic"
    rfq["variants"][1]["qty"] = 0
    problems = validate_rfq(rfq)
    assert any("Ceramic" in p for p in problems)
    assert any("positive" in p for p in problems)
