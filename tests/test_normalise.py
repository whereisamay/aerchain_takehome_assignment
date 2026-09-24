"""Normalisation is plain Python. These fixtures are real model outputs captured from one extraction run
over the six demo responses; the tests pin the arithmetic and the trap handling, not the model."""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from demand import compute_demand  # noqa: E402
from normalise import lead_days, normalise  # noqa: E402
from rfq import build_rfq  # noqa: E402
from vendors import HISTORY, VENDORS  # noqa: E402

PO = date(2026, 10, 7)
RFQ = build_rfq(compute_demand({"PS-200": 12, "PS-075": 20}, date(2027, 2, 10)), ["MAT-005"], PO, set(),
                today=date(2026, 9, 23))
V = {v["code"]: v for v in VENDORS}


def run(code, buyer=None):
    raw = json.loads((ROOT / "tests" / "fixtures" / f"extraction_{code}.json").read_text())
    hist = [h for h in HISTORY if h["vendor_code"] == code]
    out = normalise(raw, RFQ, V[code], hist, PO, buyer)
    return {r["variant"]: r for r in out["rows"]}, out


def test_lead_time_parsing():
    assert lead_days("5 weeks")[0] == 35
    assert lead_days("12–13 weeks")[0] == 91
    assert lead_days("4 wks")[0] == 28
    assert lead_days("30 days")[0] == 30
    assert lead_days(None) == (None, None)
    assert lead_days("Ex-stock")[0] == 0 and lead_days("immediately from stock")[0] == 0


def test_A_partial_high_speed_landed_45_days():
    rows, _ = run("A")
    assert rows["Heavy-duty"]["price_inr"] == 4820
    assert rows["High-speed"]["availability"].startswith("Partial 12/20")
    assert "LATE" in rows["High-speed"]["availability"]  # balance 8 at 11 weeks misses need-by
    assert rows["Standard"]["credit_days"] == 45 and rows["Standard"]["landed"] is True
    assert rows["Standard"]["cert"] == "Met"


def test_B_eur_small_print_near_equivalent_standard():
    rows, out = run("B")
    assert rows["Standard"]["price_inr"] == pytest.approx(24.50 * 98.10)
    assert rows["Standard"]["credit_days"] == 30 and "2%" in rows["Standard"]["discount"]
    assert rows["Standard"]["landed"] is False
    assert rows["Standard"]["cert"] == "Needs review"
    assert rows["Standard"]["delivery_state"] == "review"  # ex-works on the need-by date
    assert any("does NOT apply" in l["text"] for l in out["ledger"] if l["topic"] == "Volume discount")


def test_C_two_of_three_advance_terms_freight_condition_cert_expiry():
    rows, _ = run("C")
    assert rows["High-speed"]["quoted"] is False and rows["High-speed"]["price_inr"] is None
    assert rows["Standard"]["credit_days"] == pytest.approx(-15)
    assert rows["Standard"]["landed"] is False and "NOT met" in rows["Standard"]["freight"]
    assert rows["Standard"]["cert"] == "Needs review" and "expires 12 Nov 2026" in rows["Standard"]["cert_detail"]


def test_D_usd_per_set_of_two_lc_no_iso9001():
    rows, _ = run("D")
    assert rows["Standard"]["price_inr"] == pytest.approx(54 / 2 * 88.40)
    assert rows["High-speed"]["qty_offered"] == 20
    assert rows["Standard"]["credit_days"] == 0
    assert rows["Standard"]["landed"] is False
    assert rows["Standard"]["cert"] == "Not met"


def test_E_same_terms_as_last_order_goes_to_buyer_not_guessed():
    rows, out = run("E")
    assert rows["Standard"]["credit_days"] is None and rows["Standard"]["credit_state"] == "buyer"
    assert len(out["flags"]) == 1 and "No purchase order" in out["flags"][0]["evidence"]
    assert "60 days" in out["flags"][0]["evidence"]  # the partial record is shown, not used
    rows, out = run("E", {"credit_days": "45", "freight": "delivered", "credit_note": "phone"})
    assert rows["Standard"]["credit_days"] == 45 and rows["Standard"]["landed"] is True


def test_F_cheapest_but_late():
    rows, _ = run("F")
    assert rows["Standard"]["price_inr"] == 2150
    assert rows["Standard"]["delivery"].startswith("LATE")
    assert rows["Standard"]["availability"].startswith("Assumed full")


def test_recommendations_per_variant():
    from recommend import recommend
    rows = [r for v in "ABCDEF" for r in run(v)[0].values()]
    recs = {r["item"]: r for r in recommend(rows, RFQ)}
    assert recs["Standard"]["pick"]["vendor"] == "A" and recs["Standard"]["status"] == "Recommended"
    assert recs["Heavy-duty"]["pick"]["vendor"] == "A"
    hs = recs["High-speed"]
    assert hs["pick"]["vendor"] == "B" and "caveats" in hs["status"]
    assert any(line.startswith("Alternative: A") for line in hs["why"])
    assert {c["vendor"] for c in recs["Standard"]["excluded"]} == {"D", "E", "F"}  # not met / late
    assert all(0 <= r["score"] <= 1 for r in rows)
