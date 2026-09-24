"""Simulated mail transport, real mail handling.

Transport is a folder (data/outbox, data/inbox). Composition (MIME), threading, ingestion,
attachment extraction and correlation are real. Production swaps the folders for an SMTP
provider and an inbound webhook; nothing above this module changes.
"""
import hashlib
import json
import re
import shutil
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import make_msgid, parseaddr
from pathlib import Path

import db
import vendors
from master_data import BUYER
from rfq import standards_in
from rfq_xlsx import render_rfq_xlsx

BUYER_DOMAIN = "buyer.example"
DEMO_INBOX = db.DATA_DIR / "demo_inbox"
ATTACH_DIR = db.INBOX / "attachments"
TOKEN_SALT = "deccan-flow-rfq"

SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_out (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, status TEXT, rfq_id TEXT, vendor_code TEXT,
    to_addr TEXT, reply_to TEXT, subject TEXT, body TEXT, message_id TEXT, path TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS mail_in (
    id INTEGER PRIMARY KEY AUTOINCREMENT, file_hash TEXT UNIQUE, path TEXT, source TEXT,
    from_addr TEXT, to_addrs TEXT, subject TEXT, sent_date TEXT, body TEXT, attachments TEXT,
    rfq_id TEXT, vendor_code TEXT, match_method TEXT, match_detail TEXT, status TEXT, received_at TEXT
);
"""

RFQ_REF = re.compile(r"RFQ-\d{4}-MCH-\d{3}", re.I)
TOKEN_ADDR = re.compile(r"rfq\+(\d{3})-v([a-z])-([0-9a-f]{4})@" + re.escape(BUYER_DOMAIN), re.I)


def _conn():
    c = db.connect()
    c.executescript(SCHEMA)
    return c


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ correlation token
def token_for(rfq_id: str, vendor_code: str) -> str:
    return hashlib.sha256(f"{TOKEN_SALT}|{rfq_id}|{vendor_code}".encode()).hexdigest()[:4]


def reply_address(rfq_id: str, vendor_code: str) -> str:
    return f"rfq+{rfq_id[-3:]}-v{vendor_code}-{token_for(rfq_id, vendor_code)}@{BUYER_DOMAIN}".lower()


# ------------------------------------------------------------------ outbound
def _rfq_body(rfq: dict, vendor: dict) -> str:
    lines = "\n".join(
        f"  - {l['material']}{'' if l['variant'] == '—' else ' (' + l['variant'] + ')'}: {l['qty']} {l['uom']}, "
        f"needed at our plant by {datetime.fromisoformat(l['need_by']):%d %b %Y}"
        for l in rfq["lines"])
    stds = "\n".join(f"  - {m}: {q}" for m, q in standards_in(rfq))
    return f"""Dear {vendor['name']} team,

Please find attached our Request for Quotation {rfq['rfq_id']} — {rfq['title']}.

Requirement:
{lines}

Quality standards required:
{stds}
Quotes close: {datetime.fromisoformat(rfq['close_date']):%d %b %Y}

Please complete the shaded cells in the attached workbook, or reply in your own format covering:
unit price and currency, availability, delivery time (days from PO), credit terms (days from
invoice date) and quality certification (standard, certificate number, expiry).

Simply reply to this email so your quotation is routed to this RFQ.

Regards,
{BUYER['contact']}
{BUYER['company']}
{BUYER['plant']}
"""


def compose(kind: str, rfq: dict, vendor: dict, subject: str, body: str,
            attachments: list[tuple[str, bytes, str]] = ()) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{BUYER['contact']} <{BUYER['email']}>"
    msg["To"] = f"{vendor['name']} <{vendor['email']}>"
    msg["Reply-To"] = reply_address(rfq["rfq_id"], vendor["code"])
    msg["Subject"] = subject
    msg["Date"] = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    msg["Message-ID"] = make_msgid(domain=BUYER_DOMAIN)
    msg["X-RFQ-Kind"] = kind
    msg.set_content(body)
    for name, data, mime in attachments:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg


def _write_outbox(msg: EmailMessage, kind: str, status: str, rfq_id: str, vendor_code: str) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = db.OUTBOX / f"{stamp}_{kind}_{rfq_id}_v{vendor_code}.eml"
    path.write_bytes(msg.as_bytes())
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO mail_out (kind, status, rfq_id, vendor_code, to_addr, reply_to, subject, body, message_id,"
            " path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (kind, status, rfq_id, vendor_code, msg["To"], msg["Reply-To"], msg["Subject"],
             msg.get_body(("plain",)).get_content(), msg["Message-ID"], str(path), _now()))
        return cur.lastrowid


def send_rfq(rfq: dict, vendor_codes: list[str]) -> list[int]:
    pack = render_rfq_xlsx(rfq)
    by_code = {v["code"]: v for v in vendors.list_vendors()}
    ids = []
    for code in vendor_codes:
        v = by_code[code]
        msg = compose("rfq", rfq, v, f"{rfq['rfq_id']} — Request for quotation: {rfq['title']}",
                      _rfq_body(rfq, v),
                      [(f"{rfq['rfq_id']}.xlsx", pack,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")])
        ids.append(_write_outbox(msg, "rfq", "sent", rfq["rfq_id"], code))
    return ids


def outbox() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM mail_out ORDER BY id DESC")]


def set_outbox_status(mail_id: int, status: str) -> None:
    with _conn() as c:
        c.execute("UPDATE mail_out SET status=? WHERE id=?", (status, mail_id))


# ------------------------------------------------------------------ inbound
CURATED_RFQ = "RFQ-2026-MCH-005"
CURATED_FILES = {"A": "01_vendorA.eml", "B": "02_vendorB.eml", "C": "03_vendorC.eml", "D": "04_vendorD.eml",
                 "E": "05_vendorE.eml", "F": "06_unknown_sender.eml"}


def deliver_demo_replies() -> int:
    """Stand-in for vendors hitting 'reply' to every RFQ that was sent.

    The bearing-only RFQ-2026-MCH-005 gets the hand-built trap dataset. Any other RFQ gets replies
    written by the vendor simulator (vendor_sim/, outside the app) for exactly the items on that RFQ.
    Only vendors that were actually sent the RFQ reply."""
    import sys
    if str(db.ROOT) not in sys.path:
        sys.path.append(str(db.ROOT))
    from vendor_sim.simulate import simulate_replies

    sent: dict[str, set[str]] = {}
    for o in outbox():
        if o["kind"] == "rfq":
            sent.setdefault(o["rfq_id"], set()).add(o["vendor_code"])
    n = 0
    for rid, codes in sent.items():
        rfq = db.get_rfq(rid)
        if not rfq:
            continue
        if rid == CURATED_RFQ and {l["material_code"] for l in rfq["lines"]} == {"MAT-005"}:
            for v in sorted(codes):
                f = DEMO_INBOX / CURATED_FILES[v]
                dest = db.INBOX / f.name
                if f.exists() and not dest.exists():
                    shutil.copy(f, dest)
                    n += 1
            continue
        po = datetime.fromisoformat(rfq["planned_po_date"]).date() if rfq.get("planned_po_date") else None
        n += len(simulate_replies(rfq, sorted(codes), {v: reply_address(rid, v) for v in codes},
                                  po or datetime.now().date(), db.INBOX, db.DATA_DIR / "sim_files"))
    return n


def _rfq_by_number(num: str) -> str | None:
    matches = [r["rfq_id"] for r in db.list_rfqs() if r["rfq_id"].endswith(f"-{num}")]
    return matches[0] if len(matches) == 1 else None


def _vendor_by_sender(addr: str) -> dict | None:
    addr = addr.lower()
    domain = addr.rsplit("@", 1)[-1]
    vs = vendors.list_vendors()
    exact = [v for v in vs if v["email"].lower() == addr]
    if exact:
        return exact[0]
    by_domain = [v for v in vs if v["email"].lower().rsplit("@", 1)[-1] == domain]
    return by_domain[0] if len(by_domain) == 1 else None


def correlate(to_addrs: list[str], subject: str, from_addr: str) -> dict:
    """Priority: correlation token, then subject reference (+ sender for the vendor), then sender alone.
    Returns rfq_id / vendor_code / method / detail. Never guesses: if none match, both stay None."""
    sent = outbox()
    # 1. Token in any recipient address
    for a in to_addrs:
        m = TOKEN_ADDR.search(a)
        if not m:
            continue
        num, vcode, tok = m.group(1), m.group(2).upper(), m.group(3).lower()
        rfq_id = _rfq_by_number(num)
        if rfq_id and token_for(rfq_id, vcode) == tok:
            was_sent = any(s["rfq_id"] == rfq_id and s["vendor_code"] == vcode and s["kind"] == "rfq" for s in sent)
            return {"rfq_id": rfq_id, "vendor_code": vcode, "method": "token",
                    "detail": f"Reply address {a} carries a valid token for {rfq_id} / vendor {vcode}"
                              + ("" if was_sent else " (note: no send of this RFQ to this vendor is on record)")}
    vendor = _vendor_by_sender(from_addr)
    # 2. Subject line reference
    m = RFQ_REF.search(subject or "")
    if m:
        rfq_id = m.group(0).upper()
        if db.get_rfq(rfq_id) and vendor:
            return {"rfq_id": rfq_id, "vendor_code": vendor["code"], "method": "subject",
                    "detail": f"Subject references {rfq_id}; sender {from_addr} belongs to vendor {vendor['code']}"}
        if db.get_rfq(rfq_id):
            return {"rfq_id": rfq_id, "vendor_code": None, "method": None,
                    "detail": f"Subject references {rfq_id}, but sender {from_addr} is not a known vendor address"}
    # 3. Sender address alone: only if exactly one RFQ is open with that vendor
    if vendor:
        open_rfqs = sorted({s["rfq_id"] for s in sent if s["vendor_code"] == vendor["code"] and s["kind"] == "rfq"})
        if len(open_rfqs) == 1:
            return {"rfq_id": open_rfqs[0], "vendor_code": vendor["code"], "method": "sender",
                    "detail": f"Sender {from_addr} belongs to vendor {vendor['code']}, which has exactly one "
                              f"RFQ out ({open_rfqs[0]})"}
        return {"rfq_id": None, "vendor_code": vendor["code"], "method": None,
                "detail": f"Sender is vendor {vendor['code']}, but {len(open_rfqs)} RFQs are out to them — "
                          "cannot tell which one this answers"}
    return {"rfq_id": None, "vendor_code": None, "method": None,
            "detail": f"No correlation token, no RFQ reference in the subject, and sender {from_addr} "
                      "is not a known vendor address"}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "attachment"


def ingest_bytes(raw: bytes, source: str, filename: str) -> dict:
    """Parse one RFC 822 message, extract attachments, correlate, record. Idempotent by content hash."""
    h = hashlib.sha256(raw).hexdigest()[:16]
    with _conn() as c:
        existing = c.execute("SELECT * FROM mail_in WHERE file_hash=?", (h,)).fetchone()
    if existing:
        return dict(existing)
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    from_addr = parseaddr(str(msg.get("From", "")))[1]
    to_addrs = [parseaddr(str(v))[1] for k in ("To", "Cc", "Delivered-To", "X-Original-To")
                for v in (msg.get_all(k) or []) for v in str(v).split(",")]
    subject = str(msg.get("Subject", ""))
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part else ""
    out_dir = ATTACH_DIR / h
    atts = []
    for part in msg.iter_attachments():
        name = _safe(part.get_filename() or f"part-{len(atts) + 1}")
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / name
        p.write_bytes(part.get_payload(decode=True) or b"")
        atts.append({"name": name, "path": str(p), "mime": part.get_content_type(), "size": p.stat().st_size})
    corr = correlate(to_addrs, subject, from_addr)
    status = "matched" if corr["rfq_id"] and corr["vendor_code"] else "unmatched"
    with _conn() as c:
        c.execute(
            "INSERT INTO mail_in (file_hash, path, source, from_addr, to_addrs, subject, sent_date, body, attachments,"
            " rfq_id, vendor_code, match_method, match_detail, status, received_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (h, filename, source, from_addr, json.dumps(to_addrs), subject, str(msg.get("Date", "")), body,
             json.dumps(atts), corr["rfq_id"], corr["vendor_code"], corr["method"], corr["detail"], status, _now()))
        return dict(c.execute("SELECT * FROM mail_in WHERE file_hash=?", (h,)).fetchone())


def check_inbox() -> list[dict]:
    """Read every .eml in data/inbox that hasn't been ingested yet."""
    return [ingest_bytes(f.read_bytes(), "inbox", str(f)) for f in sorted(db.INBOX.glob("*.eml"))]


def ingest_upload(filename: str, data: bytes) -> dict:
    """A file dropped in the UI. An .eml goes through normal correlation; any other file has no
    headers to correlate on, so it is wrapped as a message and lands in the Unmatched queue."""
    if filename.lower().endswith(".eml"):
        dest = db.INBOX / _safe(filename)
        dest.write_bytes(data)
        return ingest_bytes(data, "upload", str(dest))
    msg = EmailMessage()
    msg["From"] = "manual-upload@local"
    msg["To"] = BUYER["email"]
    msg["Subject"] = f"Uploaded file: {filename}"
    msg["Date"] = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    msg.set_content(f"File uploaded by the buyer through the Mail screen: {filename}")
    import mimetypes
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    maintype, subtype = mime.split("/", 1)
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    raw = msg.as_bytes()
    dest = db.INBOX / f"upload_{datetime.now():%Y%m%d-%H%M%S}_{_safe(filename)}.eml"
    dest.write_bytes(raw)
    return ingest_bytes(raw, "upload", str(dest))


def inbox(status: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM mail_in", ()
    if status:
        q, args = q + " WHERE status=?", (status,)
    with _conn() as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY id", args)]
    for r in rows:
        r["attachments"] = json.loads(r["attachments"] or "[]")
        r["to_addrs"] = json.loads(r["to_addrs"] or "[]")
    return rows


def assign(mail_id: int, rfq_id: str, vendor_code: str) -> None:
    with _conn() as c:
        c.execute("UPDATE mail_in SET rfq_id=?, vendor_code=?, status='assigned', match_method='manual',"
                  " match_detail=match_detail || ' → assigned manually by buyer' WHERE id=?",
                  (rfq_id, vendor_code, mail_id))


def reset_outbox() -> None:
    """Clear everything sent (the RFQ Sender's history); the inbox is untouched."""
    with _conn() as c:
        c.execute("DELETE FROM mail_out")
    shutil.rmtree(db.OUTBOX, ignore_errors=True)
    db.OUTBOX.mkdir(parents=True, exist_ok=True)


def reset_mail() -> None:
    """Demo reset: clear both queues and the folders."""
    with _conn() as c:
        c.execute("DELETE FROM mail_in")
        c.execute("DELETE FROM mail_out")
    for d in (db.INBOX, db.OUTBOX):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
