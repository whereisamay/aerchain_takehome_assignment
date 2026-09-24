"""Live evaluation: re-extract the six demo responses to RFQ-2026-MCH-005 with the configured model and check
the result against the answer key in dataset/DATASET_NOTES.md. Makes six real model calls.

Prerequisite: RFQ-2026-MCH-005 sent to A-F and the demo replies in the inbox (F assigned).
Run: python evals/extraction_eval.py
"""
import sys, time, json; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app"))
from datetime import date
import db, analysis, llm
from recommend import verdicts
print("model:", llm.model_name())
rfq = db.get_rfq("RFQ-2026-MCH-005"); msgs = analysis.responses_for(rfq["rfq_id"])
t = time.time(); res = analysis.run_extractions(msgs)
print(f"extracted {len(msgs)} in {time.time()-t:.0f}s; errors:", [(m["vendor_code"], e) for m, e in res if e])
for m in msgs:
    e = analysis.get_extraction(m["id"]); print(f"  {m['vendor_code']}: {e['meta']['seconds']}s")
b = analysis.build(rfq, msgs, date(2026, 10, 7)); vd = verdicts(b["rows"], rfq)
R = {(r["vendor"], r["variant"]): r for r in b["rows"]}
checks = [
 ("A HD price 4820", R["A","Heavy-duty"]["price_inr"] == 4820),
 ("A HS partial 12/20, balance LATE", R["A","High-speed"]["availability"].startswith("Partial 12/20") and "LATE" in R["A","High-speed"]["availability"]),
 ("A 45 days, landed, Met", R["A","Standard"]["credit_days"] == 45 and R["A","Standard"]["landed"] is True and R["A","Standard"]["cert"] == "Met"),
 ("B EUR 24.50 -> 2403.45", abs((R["B","Standard"]["price_inr"] or 0) - 2403.45) < 0.01),
 ("B 30 days + 2% discount", R["B","Standard"]["credit_days"] == 30 and "2" in (R["B","Standard"]["discount"] or "")),
 ("B ex-works, not landed", R["B","Standard"]["landed"] is False),
 ("B DIN 628 -> Needs review", R["B","Standard"]["cert"] == "Needs review"),
 ("B volume rebate not applied", any("does NOT apply" in l["text"] for l in b["ledger"] if l["vendor"] == "B")),
 ("C HS not quoted", R["C","High-speed"]["quoted"] is False and R["C","High-speed"]["price_inr"] is None),
 ("C credit ~ -15", R["C","Standard"]["credit_days"] is not None and abs(R["C","Standard"]["credit_days"] + 15) < 0.1),
 ("C free freight NOT met", R["C","Standard"]["landed"] is False and "NOT met" in R["C","Standard"]["freight"]),
 ("C cert expiry before need-by", R["C","Standard"]["cert"] == "Needs review" and "12 Nov" in R["C","Standard"]["cert_detail"]),
 ("D USD per set of 2 -> 2386.80", abs((R["D","Standard"]["price_inr"] or 0) - 2386.80) < 0.01),
 ("D HS qty 20 units", R["D","High-speed"]["qty_offered"] == 20),
 ("D LC at sight 0 days", R["D","Standard"]["credit_days"] == 0),
 ("D no ISO 9001 -> Not met", R["D","Standard"]["cert"] == "Not met"),
 ("E credit left for buyer", R["E","Standard"]["credit_days"] is None and R["E","Standard"]["credit_state"] == "buyer"),
 ("E flag shows unawarded 60-day quote", any(f["vendor"] == "E" and "60 days" in f["evidence"] for f in b["flags"])),
 ("F price 2150 from photo", R["F","Standard"]["price_inr"] == 2150),
 ("F LATE", (R["F","Standard"]["delivery"] or "").startswith("LATE")),
 ("Rec: Standard -> A", vd[(0, "A")]["kind"] == "pick"),
 ("Rec: Heavy-duty -> A", vd[(1, "A")]["kind"] == "pick"),
 ("Rec: High-speed -> B", vd[(2, "B")]["kind"] == "pick"),
]
for name, ok in checks: print(("PASS " if ok else "FAIL ") + name)
print(f"{sum(ok for _, ok in checks)}/{len(checks)} passed")
for (v, var), r in sorted(R.items()):
    print(f"   {v} {var:<10} ₹{r['price_inr'] or 0:>8,.2f} | {r['availability'][:25]:<25} | cr {r['credit_days']} {r['credit_state']:<7} | {r['cert']:<12} | {str(r['delivery'])[:30]}")
