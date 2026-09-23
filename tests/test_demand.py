import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from demand import compute_demand, po_date_for  # noqa: E402

TARGET = date(2027, 2, 10)


def _q(df, mat, variant):
    row = df[(df["Material code"] == mat) & (df["Variant"] == variant)]
    return int(row["Total qty"].iloc[0]) if len(row) else 0


def test_acceptance_case():
    df = compute_demand({"PS-200": 12, "PS-075": 20}, TARGET)
    assert po_date_for(TARGET) == date(2026, 10, 7)
    # Shared single-variant materials sum across machines
    assert _q(df, "MAT-001", "—") == 32
    # Fastener kits: 2 per PS-200, 1 per PS-075
    assert _q(df, "MAT-010", "—") == 12 * 2 + 20
    # Bearing variant split follows which machine takes which variant
    assert _q(df, "MAT-005", "Heavy-duty") == 24
    assert _q(df, "MAT-005", "Standard") == 40
    assert _q(df, "MAT-005", "High-speed") == 20
    assert _q(df, "MAT-007", "200 kW") == 12
    assert _q(df, "MAT-007", "75 kW") == 20
    assert _q(df, "MAT-007", "110 kW") == 0
    assert _q(df, "MAT-006", "Double cartridge") == 12
    assert _q(df, "MAT-006", "Single cartridge") == 20
    # Need-by = start of phase
    need = dict(zip(df["Phase"], df["Need-by"]))
    assert need == {"P1": date(2026, 10, 7), "P2": date(2026, 11, 18),
                    "P3": date(2026, 12, 16), "P4": date(2027, 1, 13)}


def test_zero_rows_hidden_by_default():
    df = compute_demand({"PS-200": 12, "PS-075": 0}, TARGET)
    assert _q(df, "MAT-005", "Standard") == 0
    assert "Standard" not in set(df[df["Material code"] == "MAT-005"]["Variant"])
    full = compute_demand({"PS-200": 12, "PS-075": 0}, TARGET, include_zero=True)
    assert len(full) > len(df)
