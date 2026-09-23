"""Vendor master and purchase history. Profile data only — nothing here describes what a
vendor quoted; that comes exclusively from reading their response documents."""
import json

import db

VENDORS = [
    {"code": "A", "name": "Shreeji Bearings & Transmission Pvt Ltd", "city": "Rajkot",
     "email": "sales@shreeji-bearings.example", "has_reference": True,
     "referred_by": "Head of Manufacturing", "supplied_since": "2021"},
    {"code": "B", "name": "Nordlager Antriebstechnik GmbH", "city": "Stuttgart",
     "email": "export@nordlager.example", "has_reference": True,
     "referred_by": "Peer buyer at another plant", "supplied_since": None},
    {"code": "C", "name": "Kaveri Precision Components", "city": "Coimbatore",
     "email": "info@kaveriprecision.example", "has_reference": False,
     "referred_by": None, "supplied_since": None},
    {"code": "D", "name": "Pacific Motion Components Pte Ltd", "city": "Singapore",
     "email": "quotes@pacificmotion.example", "has_reference": False,
     "referred_by": None, "supplied_since": None},
    {"code": "E", "name": "Vardhman Industrial Supplies", "city": "Pune",
     "email": "rakesh@vardhman-ind.example", "has_reference": True,
     "referred_by": "Managing Director", "supplied_since": None},
    {"code": "F", "name": "Sri Lakshmi Bearing House", "city": "Chennai",
     "email": "slbh.chennai@mail.example", "has_reference": False,
     "referred_by": None, "supplied_since": None},
]

# What the buyer's own records hold. Deliberately incomplete: this is what the system
# has to work with when a vendor refers to "our last order".
HISTORY = [
    {"vendor_code": "A", "doc_type": "PO", "ref": "PO-2024-0412", "date": "2024-06-11",
     "material": "Bearing assembly (Standard)", "awarded": True,
     "terms": {"credit": "45 days net from invoice", "freight": "Delivered to plant", "currency": "INR"}},
    {"vendor_code": "A", "doc_type": "PO", "ref": "PO-2025-0877", "date": "2025-08-02",
     "material": "Bearing assembly (Heavy-duty)", "awarded": True,
     "terms": {"credit": "45 days net from invoice", "freight": "Delivered to plant", "currency": "INR"}},
    {"vendor_code": "E", "doc_type": "Quotation", "ref": "VIS/Q/25-26/0193", "date": "2025-03-14",
     "material": "Fastener and gasket kit", "awarded": False,
     "terms": {"credit": "60 days", "freight": None, "currency": "INR"},
     "note": "Quotation received for a fastener RFQ; not awarded. No purchase order was ever placed with this vendor."},
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    code TEXT PRIMARY KEY, name TEXT, city TEXT, email TEXT,
    has_reference INTEGER, referred_by TEXT, supplied_since TEXT
);
CREATE TABLE IF NOT EXISTS vendor_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_code TEXT, doc_type TEXT, ref TEXT UNIQUE,
    date TEXT, material TEXT, awarded INTEGER, terms TEXT, note TEXT
);
"""


def seed() -> None:
    with db.connect() as c:
        c.executescript(SCHEMA)
        for v in VENDORS:
            c.execute("INSERT OR REPLACE INTO vendors VALUES (?,?,?,?,?,?,?)",
                      (v["code"], v["name"], v["city"], v["email"], int(v["has_reference"]),
                       v["referred_by"], v["supplied_since"]))
        for h in HISTORY:
            c.execute("INSERT OR REPLACE INTO vendor_history (vendor_code, doc_type, ref, date, material, awarded,"
                      " terms, note) VALUES (?,?,?,?,?,?,?,?)",
                      (h["vendor_code"], h["doc_type"], h["ref"], h["date"], h["material"], int(h["awarded"]),
                       json.dumps(h["terms"]), h.get("note")))


def list_vendors() -> list[dict]:
    seed()
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM vendors ORDER BY code")]


def history(vendor_code: str | None = None) -> list[dict]:
    seed()
    q, args = "SELECT * FROM vendor_history", ()
    if vendor_code:
        q, args = q + " WHERE vendor_code=?", (vendor_code,)
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY date", args)]
    for r in rows:
        r["terms"] = json.loads(r["terms"])
    return rows
