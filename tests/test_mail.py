import json
import sys
from datetime import date
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import db  # noqa: E402
import mail  # noqa: E402
from demand import compute_demand, po_date_for  # noqa: E402
from master_data import MATERIALS  # noqa: E402
from rfq import build_rfq  # noqa: E402

TARGET = date(2027, 2, 10)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(db, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(mail, "ATTACH_DIR", tmp_path / "inbox" / "attachments")
    monkeypatch.setattr(mail, "DEMO_INBOX", ROOT / "data" / "demo_inbox")
    demand = compute_demand({"PS-200": 12, "PS-075": 20}, TARGET)
    for code in ("MAT-005", "MAT-006"):
        db.save_rfq(build_rfq(demand, [code], po_date_for(TARGET), set(), today=date(2026, 9, 23)), "generated")
    return tmp_path


def test_send_composes_real_mime_with_pack_and_token(sandbox):
    rfq = db.get_rfq("RFQ-2026-MCH-005")
    ids = mail.send_rfq(rfq, list("ABCDEF"))
    assert len(ids) == 6
    out = mail.outbox()
    a = next(o for o in out if o["vendor_code"] == "A")
    msg = BytesParser(policy=policy.default).parsebytes(Path(a["path"]).read_bytes())
    assert msg["Reply-To"] == mail.reply_address("RFQ-2026-MCH-005", "A")
    assert "rfq+005-va-" in msg["Reply-To"]
    atts = list(msg.iter_attachments())
    assert atts[0].get_filename() == "RFQ-2026-MCH-005.xlsx"
    body = msg.get_body(("plain",)).get_content()
    assert "RFQ-2026-MCH-005" in body and "18 Nov 2026" in body


def test_demo_replies_correlate_by_each_path_and_unknown_goes_unmatched(sandbox):
    mail.send_rfq(db.get_rfq("RFQ-2026-MCH-005"), list("ABCDEF"))
    assert mail.deliver_demo_replies() == 6
    rows = {Path(r["path"]).name[:2]: r for r in mail.check_inbox()}
    assert (rows["01"]["vendor_code"], rows["01"]["match_method"]) == ("A", "token")
    assert (rows["02"]["vendor_code"], rows["02"]["match_method"]) == ("B", "subject")
    assert (rows["03"]["vendor_code"], rows["03"]["match_method"]) == ("C", "token")
    assert (rows["04"]["vendor_code"], rows["04"]["match_method"]) == ("D", "sender")
    assert (rows["05"]["vendor_code"], rows["05"]["match_method"]) == ("E", "token")
    assert rows["06"]["status"] == "unmatched" and rows["06"]["vendor_code"] is None
    assert all(r["rfq_id"] == "RFQ-2026-MCH-005" for k, r in rows.items() if k != "06")
    # attachments extracted to disk; E has none
    assert Path(mail.inbox()[0]["attachments"][0]["path"]).exists()
    assert rows["05"]["attachments"] == "[]"
    # ingest is idempotent
    assert len(mail.check_inbox()) == 6 and len(mail.inbox()) == 6
    # manual assignment clears the unmatched queue
    mail.assign(rows["06"]["id"], "RFQ-2026-MCH-005", "F")
    assert mail.inbox("unmatched") == []


def test_forged_token_does_not_match(sandbox):
    corr = mail.correlate(["rfq+005-va-0000@buyer.example"], "hello", "someone@nowhere.example")
    assert corr["rfq_id"] is None and corr["vendor_code"] is None


def test_sender_with_several_open_rfqs_is_not_guessed(sandbox):
    for rid in ("RFQ-2026-MCH-005", "RFQ-2026-MCH-006"):
        mail.send_rfq(db.get_rfq(rid), ["D"])
    corr = mail.correlate(["procurement@buyer.example"], "our quote", "sales.asia@pacificmotion.example")
    assert corr["vendor_code"] == "D" and corr["rfq_id"] is None


def test_multi_material_rfq_mail_lists_every_line(sandbox):
    demand = compute_demand({"PS-200": 12, "PS-075": 20}, TARGET)
    rfq = build_rfq(demand, [c for c, m in MATERIALS.items() if m.phase == "P1"], po_date_for(TARGET), set())
    db.save_rfq(rfq, "generated")
    mail.send_rfq(rfq, ["A"])
    body = mail.outbox()[0]["body"]
    assert "Pump casing, cast iron: 32 nos" in body and "AISI 431" in body
    assert mail.correlate([mail.reply_address(rfq["rfq_id"], "A")], "", "x@y.example")["rfq_id"] == rfq["rfq_id"]


def test_uploaded_loose_file_lands_unmatched(sandbox):
    r = mail.ingest_upload("photo.jpg", b"\xff\xd8\xff fake jpeg")
    assert r["status"] == "unmatched"
    assert '"photo.jpg"' in r["attachments"]


def test_simulated_replies_answer_whatever_rfq_was_sent(sandbox):
    demand = compute_demand({"PS-200": 12, "PS-075": 20}, TARGET)
    rfq = build_rfq(demand, [c for c, m in MATERIALS.items() if m.phase == "P3"], po_date_for(TARGET), set())
    db.save_rfq(rfq, "generated")
    mail.send_rfq(rfq, ["A", "B", "C", "E", "F"])  # only the vendors that were sent should reply (not D)
    assert mail.deliver_demo_replies() == 5
    rows = mail.check_inbox()
    matched = {r["vendor_code"] for r in rows if r["status"] == "matched"}
    assert matched == {"A", "B", "C", "E"} and all(r["rfq_id"] == rfq["rfq_id"] for r in rows if r["vendor_code"])
    assert sum(r["status"] == "unmatched" for r in rows) == 1  # F writes from personal webmail
    assert any(json.loads(r["attachments"])[0]["name"].endswith(".pdf") for r in rows if r["vendor_code"] == "B")
    a = next(r for r in rows if r["vendor_code"] == "A")
    assert json.loads(a["attachments"])[0]["name"].endswith(".xlsx")
