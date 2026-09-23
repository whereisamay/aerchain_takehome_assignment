"""Hardcoded master data: machines, phases, materials, variants, BOM, quality standards.

No AI here. Everything downstream (demand, RFQs, normalisation) keys off these codes.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Machine:
    code: str
    name: str


@dataclass(frozen=True)
class Phase:
    code: str
    name: str
    start_week: int  # weeks after PO
    duration_weeks: int


@dataclass(frozen=True)
class Material:
    no: int
    code: str
    name: str
    phase: str
    uom: str
    quality_standard: str
    # Variant names in display order. A material without variants has a single "Standard" entry.
    variants: tuple[str, ...] = ("—",)
    spec_notes: str = ""


MACHINES: dict[str, Machine] = {
    "PS-200": Machine("PS-200", "200 kW industrial centrifugal pump skid"),
    "PS-075": Machine("PS-075", "75 kW inline booster pump skid"),
}

PHASES: dict[str, Phase] = {
    "P1": Phase("P1", "Casting and machining", start_week=0, duration_weeks=6),
    "P2": Phase("P2", "Rotating assembly", start_week=6, duration_weeks=4),
    "P3": Phase("P3", "Drive and controls", start_week=10, duration_weeks=4),
    "P4": Phase("P4", "Final assembly and testing", start_week=14, duration_weeks=4),
}

# PO date -> machine completion. The last phase ends here.
BUILD_WEEKS = max(p.start_week + p.duration_weeks for p in PHASES.values())

MATERIALS: dict[str, Material] = {m.code: m for m in [
    Material(1, "MAT-001", "Pump casing, cast iron", "P1", "nos",
             "IS 210 Gr FG260 + foundry ISO 9001",
             spec_notes="Volute casing, hydro-tested to 1.5x design pressure"),
    Material(2, "MAT-002", "Impeller casting, aluminium bronze", "P1", "nos",
             "IS 305 + material test certificate",
             spec_notes="Closed impeller, dynamically balanced after machining"),
    Material(3, "MAT-003", "Base frame, fabricated steel", "P1", "nos",
             "IS 2062 E350 BR",
             spec_notes="Common skid base for pump and motor, grout holes, lifting lugs"),
    Material(4, "MAT-004", "Pump shaft, stainless", "P1", "nos",
             "AISI 431 + hardness test report",
             spec_notes="Hardened and ground at bearing and seal journals"),
    Material(5, "MAT-005", "Bearing assembly", "P2", "nos",
             "ISO 15243 + supplier ISO 9001",
             variants=("Standard", "Heavy-duty", "High-speed"),
             spec_notes="Priced per bearing assembly (one bearing), not per set"),
    Material(6, "MAT-006", "Mechanical seal", "P2", "nos",
             "API 682 4th ed.",
             variants=("Single cartridge", "Double cartridge")),
    Material(7, "MAT-007", "Motor assembly", "P2", "nos",
             "IE3 efficiency, IS 12615 + IEC 60034-30",
             variants=("200 kW", "110 kW", "75 kW"),
             spec_notes="415 V, 50 Hz, TEFC, foot mounted"),
    Material(8, "MAT-008", "Coupling and guard", "P3", "sets",
             "IS 2062 + dynamic balance G6.3"),
    Material(9, "MAT-009", "Control panel with VFD", "P3", "nos",
             "IEC 61439-1 + CE marking"),
    Material(10, "MAT-010", "Fastener and gasket kit", "P4", "kits",
             "IS 1367 property class 8.8"),
]}

# Per-unit BOM: machine -> list of (material code, variant, qty per machine).
# The two skids share materials but differ in quantity and variant, which is what
# makes the demand split non-trivial (e.g. bearings: PS-200 is heavy-duty only,
# PS-075 runs standard bearings plus a high-speed thrust bearing).
BOM: dict[str, list[tuple[str, str, int]]] = {
    "PS-200": [
        ("MAT-001", "—", 1),
        ("MAT-002", "—", 1),
        ("MAT-003", "—", 1),
        ("MAT-004", "—", 1),
        ("MAT-005", "Heavy-duty", 2),
        ("MAT-006", "Double cartridge", 1),
        ("MAT-007", "200 kW", 1),
        ("MAT-008", "—", 1),
        ("MAT-009", "—", 1),
        ("MAT-010", "—", 2),
    ],
    "PS-075": [
        ("MAT-001", "—", 1),
        ("MAT-002", "—", 1),
        ("MAT-003", "—", 1),
        ("MAT-004", "—", 1),
        ("MAT-005", "Standard", 2),
        ("MAT-005", "High-speed", 1),
        ("MAT-006", "Single cartridge", 1),
        ("MAT-007", "75 kW", 1),
        ("MAT-008", "—", 1),
        ("MAT-009", "—", 1),
        ("MAT-010", "—", 1),
    ],
}

BUYER = {
    "company": "Deccan Flow Systems Pvt Ltd",
    "plant": "Plot 42, MIDC Chakan Phase II, Pune 410501, India",
    "contact": "Procurement Desk",
    "email": "procurement@buyer.example",
    "phone": "+91 20 0000 0000",
    "gstin": "27AAAAA0000A1Z5",
}


def _validate() -> None:
    for machine, lines in BOM.items():
        assert machine in MACHINES, machine
        for mat, variant, qty in lines:
            assert mat in MATERIALS, mat
            assert variant in MATERIALS[mat].variants, (mat, variant)
            assert qty > 0
    for m in MATERIALS.values():
        assert m.phase in PHASES, m.phase


_validate()
