"""Glue for screen 5: stored extractions, buyer inputs, and the comparison built from them."""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date

import db
import mail
import vendors
from extract import _walk_fields, extract, get_field, review_items
from normalise import normalise

SCHEMA = """
CREATE TABLE IF NOT EXISTS extractions (
    mail_id INTEGER PRIMARY KEY, rfq_id TEXT, vendor_code TEXT, body TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS field_reviews (
    mail_id INTEGER, path TEXT, action TEXT, value TEXT, created_at TEXT, PRIMARY KEY (mail_id, path)
);
CREATE TABLE IF NOT EXISTS buyer_inputs (
    mail_id INTEGER, key TEXT, value TEXT, created_at TEXT, PRIMARY KEY (mail_id, key)
);
"""


def _conn():
    c = db.connect()
    c.executescript(SCHEMA)
    return c


def responses_for(rfq_id: str) -> list[dict]:
    """Inbound messages matched or assigned to this RFQ, with extraction status."""
    ex = {r["mail_id"]: r for r in list_extractions()}
    out = []
    for m in mail.inbox():
        if m["rfq_id"] == rfq_id and m["vendor_code"]:
            m["extracted"] = m["id"] in ex
            m["extracted_at"] = ex[m["id"]]["created_at"] if m["id"] in ex else None
            out.append(m)
    return out


def list_extractions() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT mail_id, rfq_id, vendor_code, created_at FROM extractions")]


def get_extraction(mail_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT body FROM extractions WHERE mail_id=?", (mail_id,)).fetchone()
    return json.loads(r["body"]) if r else None


def save_extraction(msg: dict, result: dict) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO extractions VALUES (?,?,?,?,?)",
                  (msg["id"], msg["rfq_id"], msg["vendor_code"], json.dumps(result, ensure_ascii=False),
                   result["meta"]["extracted_at"]))
        c.execute("DELETE FROM field_reviews WHERE mail_id=?", (msg["id"],))


def run_extractions(msgs: list[dict], on_done=None) -> list[tuple[dict, str | None]]:
    """Extract several responses in parallel. Returns [(msg, error or None)]."""
    vs = {v["code"]: v for v in vendors.list_vendors()}
    results = []

    def job(m):
        return extract(m, db.get_rfq(m["rfq_id"]), vs[m["vendor_code"]])

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(job, m): m for m in msgs}
        for f in as_completed(futs):
            m = futs[f]
            try:
                save_extraction(m, f.result())
                results.append((m, None))
            except Exception as e:  # noqa: BLE001 — surface any failure per message in the UI
                results.append((m, str(e)))
            if on_done:
                on_done(m, results[-1][1])
    return results


# ------------------------------------------------------------------ buyer review of low-confidence fields
def set_review(mail_id: int, path: str, action: str, value: str | None = None) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO field_reviews VALUES (?,?,?,?,datetime('now'))",
                  (mail_id, path, action, value))


def reviews(mail_id: int) -> dict:
    with _conn() as c:
        return {r["path"]: dict(r) for r in c.execute("SELECT * FROM field_reviews WHERE mail_id=?", (mail_id,))}


def apply_reviews(raw: dict, mail_id: int) -> dict:
    raw = deepcopy(raw)
    for path, r in reviews(mail_id).items():
        try:
            f = get_field(raw, path)
        except (KeyError, IndexError, ValueError):
            continue
        f["confirmed"] = True
        if r["action"] == "correct":
            f["value"] = r["value"]
            f["assumption"] = f"Corrected by buyer (was: {f.get('value')})"
            f["source"] = (f.get("source") or "") + " — corrected by buyer"
    return raw


# ------------------------------------------------------------------ buyer answers to open questions
def set_buyer_input(mail_id: int, key: str, value: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO buyer_inputs VALUES (?,?,?,datetime('now'))", (mail_id, key, value))


def clear_buyer_inputs(mail_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM buyer_inputs WHERE mail_id=?", (mail_id,))


def buyer_inputs(mail_id: int) -> dict:
    with _conn() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT * FROM buyer_inputs WHERE mail_id=?", (mail_id,))}


# ------------------------------------------------------------------ comparison
def build(rfq: dict, msgs: list[dict], po_date: date) -> dict:
    vs = {v["code"]: v for v in vendors.list_vendors()}
    rows, ledger, flags, queue, docs = [], [], [], [], {}
    for m in msgs:
        ext = get_extraction(m["id"])
        if not ext:
            continue
        raw = apply_reviews(ext["raw"], m["id"])
        n = normalise(raw, rfq, vs[m["vendor_code"]], vendors.history(m["vendor_code"]), po_date,
                      buyer_inputs(m["id"]))
        for r in n["rows"]:
            r["mail_id"] = m["id"]
        rows += n["rows"]
        ledger += n["ledger"]
        for f in n["flags"]:
            f["mail_id"] = m["id"]
        flags += n["flags"]
        for d in ext["discarded"]:
            ledger.append({"vendor": m["vendor_code"], "topic": "Discarded",
                           "text": f"{d['field']} = {d['value']!r} discarded: no source given."})
        for it in review_items(raw):
            if not raw_confirmed(raw, it["field"]):
                queue.append({"mail_id": m["id"], "vendor": m["vendor_code"], **it})
        docs[m["id"]] = {"msg": m, "ext": ext, "raw": raw}
    return {"rows": rows, "ledger": ledger, "flags": flags, "queue": queue, "docs": docs}


def raw_confirmed(raw: dict, path: str) -> bool:
    try:
        return bool(get_field(raw, path).get("confirmed"))
    except (KeyError, IndexError, ValueError):
        return False


__all__ = ["_walk_fields"]
