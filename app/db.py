"""Plain sqlite3 storage in data/app.db. No ORM."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "app.db"
INBOX = DATA_DIR / "inbox"
OUTBOX = DATA_DIR / "outbox"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rfqs (
    rfq_id        TEXT PRIMARY KEY,
    material_code TEXT NOT NULL,       -- comma-joined material codes on the RFQ
    body          TEXT NOT NULL,       -- RFQ JSON
    origin        TEXT NOT NULL,       -- generated | copilot | edited
    updated_at    TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    for d in (DATA_DIR, INBOX, OUTBOX):
        d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def save_rfq(rfq: dict, origin: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO rfqs (rfq_id, material_code, body, origin, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(rfq_id) DO UPDATE SET body=excluded.body, origin=excluded.origin, "
            "updated_at=excluded.updated_at, material_code=excluded.material_code",
            (rfq["rfq_id"], ",".join(dict.fromkeys(l["material_code"] for l in rfq["lines"])),
             json.dumps(rfq), origin, _now()))


def list_rfqs() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM rfqs ORDER BY rfq_id").fetchall()
    out = [{**json.loads(r["body"]), "_origin": r["origin"], "_updated_at": r["updated_at"]} for r in rows]
    return [r for r in out if "lines" in r]  # ignore RFQs saved in the old single-material format


def delete_rfq(rfq_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM rfqs WHERE rfq_id=?", (rfq_id,))


def purge_legacy_rfqs() -> None:
    with connect() as c:
        for r in c.execute("SELECT rfq_id, body FROM rfqs").fetchall():
            if "lines" not in json.loads(r["body"]):
                c.execute("DELETE FROM rfqs WHERE rfq_id=?", (r["rfq_id"],))


def get_rfq(rfq_id: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT body FROM rfqs WHERE rfq_id=?", (rfq_id,)).fetchone()
    body = json.loads(r["body"]) if r else None
    return body if body and "lines" in body else None
