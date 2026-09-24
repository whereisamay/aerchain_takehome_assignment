"""Normalisation: plain Python over the extraction output. No model calls.

Produces one comparable row per vendor per RFQ line, and writes every conversion or assumption
to an assumptions ledger. States per value:
  clean   — stated in the document, read with confidence, no interpretation
  assumed — a stated basis was converted, or a documented assumption was applied
  review  — low-confidence read, or a verdict that needs a human (near-equivalent standard, …)
  buyer   — cannot be determined from the document; the buyer must answer
  missing — the document does not say
"""
import re
from datetime import date, timedelta

from extract import REVIEW_THRESHOLD
from master_data import FX_AS_OF, FX_RATES

STATE_RANK = {"clean": 0, "assumed": 1, "review": 2, "buyer": 3, "missing": 4}


def _num(v) -> float | None:
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _fstate(f: dict | None) -> str:
    if not f or f.get("value") in (None, ""):
        return "missing"
    if float(f.get("confidence") or 0) < REVIEW_THRESHOLD and not f.get("confirmed"):
        return "review"
    return "clean"


def _worst(*states: str) -> str:
    return max(states, key=lambda s: STATE_RANK[s]) if states else "clean"


STATE_FACTOR = {"clean": 1.0, "assumed": 0.85, "review": 0.6, "buyer": 0.3, "missing": 0.0}


def param_score(params: dict) -> float:
    """Confidence over the six comparison parameters only: mean of (model read confidence × state factor).
    A parameter the vendor didn't state scores 0; one the buyer must decide scores low."""
    return sum(conf * STATE_FACTOR[state] for state, conf in params.values()) / len(params)


def lead_days(text: str | None) -> tuple[int | None, str | None]:
    """'12-13 weeks' → (91, note). Upper bound of a range, conservatively."""
    if not text:
        return None, None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:(?:-|–|to)\s*(\d+(?:\.\d+)?))?\s*(days?|d\b|weeks?|wks?|w\b|months?|mths?)",
                  text, re.I)
    if not m:
        return None, None
    lo, hi, unit = float(m.group(1)), float(m.group(2) or m.group(1)), m.group(3).lower()
    factor = 7 if unit.startswith("w") else 30 if unit.startswith("m") else 1
    days = int(round(hi * factor))
    note = None
    if factor != 1 or m.group(2):
        note = f"'{text}' → {days} days" + (" (upper bound of the range, conservatively)" if m.group(2) else "")
    return days, note


def _fmt_money(x: float, cur: str) -> str:
    return f"{cur} {x:,.2f}"


def normalise(ext: dict, rfq: dict, vendor: dict, history: list[dict], po_date: date,
              buyer_inputs: dict | None = None) -> dict:
    """ext: extraction output (raw). Returns {'rows', 'ledger', 'flags', 'vendor_summary'}."""
    buyer_inputs = buyer_inputs or {}
    code = vendor["code"]
    ledger, flags = [], []

    def log(topic, text):
        ledger.append({"vendor": code, "topic": topic, "text": text})

    ref = (f"Yes — {vendor['referred_by']}" + (f" (supplied since {vendor['supplied_since']})"
                                                if vendor["supplied_since"] else " (no supply history)")
           if vendor["has_reference"] else "No")

    # ---------------------------------------------------------------- lines → RFQ lines
    by_line: dict[int, dict] = {}
    for l in ext["lines"]:
        if l["rfq_line"] is not None and 0 <= l["rfq_line"] < len(rfq["lines"]):
            by_line.setdefault(l["rfq_line"], l)
            if l["mapping"] == "inferred":
                rl = rfq["lines"][l["rfq_line"]]
                log("Item mapping", f"'{l['vendor_item']}' mapped to {rl['material']} / {rl['variant']} — inferred: "
                                    f"{l['mapping_note']}")

    # ---------------------------------------------------------------- freight (vendor level)
    fr = ext["freight"]
    fkind = fr["kind"]
    order_value_inr = 0.0
    prices_inr: dict[int, float] = {}
    price_notes: dict[int, tuple[str, str]] = {}
    for idx, l in by_line.items():
        if not l["quoted"]:
            continue
        price, cur = _num(l["unit_price"]["value"]), (l["currency"]["value"] or "").upper().strip()
        units = _num(l["units_per_price"]["value"]) or None
        if price is None or not cur:
            continue
        if cur in ("RS", "RS.", "₹", "INR."):
            cur = "INR"
        if cur not in FX_RATES:
            price_notes[idx] = ("review", f"Currency '{cur}' has no stated FX rate")
            continue
        per_unit = price / (units or 1)
        inr = per_unit * FX_RATES[cur]
        prices_inr[idx] = inr
        parts = []
        if cur != "INR":
            parts.append(f"converted at 1 {cur} = {FX_RATES[cur]:.2f} INR ({FX_AS_OF})")
        if units and units != 1:
            parts.append(f"divided by {units:g} to per-unit basis ('{l['units_per_price']['value']}' per priced "
                         f"unit, {l['units_per_price']['source']})")
        if not l["units_per_price"]["value"]:
            parts.append("price basis not stated; assumed per unit as the RFQ asks")
        if parts:
            log("Price", f"{_fmt_money(price, cur)} per vendor unit → INR {inr:,.2f} per unit: " + "; ".join(parts))
        price_notes[idx] = ("assumed" if parts else "clean", _fmt_money(price, cur)
                            + (f" / {units:g} units" if units and units != 1 else ""))
        req = rfq["lines"][idx]["qty"]
        offered = _num(l["qty_offered"]["value"])
        q = min(req, offered * (units or 1)) if offered is not None else req
        order_value_inr += q * inr

    thr = _num(fr["threshold_amount"]["value"])
    thr_cur = (fr["threshold_currency"]["value"] or "INR").upper()
    if fkind == "delivered":
        landed, fnote, fstate = True, "Landed — delivered to plant", _fstate(fr["text"])
    elif fkind == "ex_works":
        landed, fnote, fstate = False, "Not landed — ex-works; freight extra (not estimated)", "assumed"
        log("Freight", f"'{fr['text']['value']}' → ex-works. Freight, insurance and duties are extra and not "
                       "estimated; prices are not landed.")
    elif fkind == "conditional" and thr is not None and thr_cur in FX_RATES:
        thr_inr = thr * FX_RATES[thr_cur]
        met = order_value_inr >= thr_inr
        landed = met
        fnote = (f"Conditional — {'met' if met else 'NOT met'}: order ≈ INR {order_value_inr:,.0f} vs threshold "
                 f"INR {thr_inr:,.0f}")
        fstate = "assumed"
        log("Freight", f"'{fr['text']['value']}' — order value at quoted prices ≈ INR {order_value_inr:,.0f} "
                       f"(RFQ qty × unit price) vs threshold {_fmt_money(thr, thr_cur)}: condition "
                       f"{'met → treated as landed' if met else 'not met → freight at actuals, not landed'}.")
    elif fkind == "conditional":
        landed, fnote, fstate = None, "Conditional — threshold unclear", "review"
    else:
        landed, fnote, fstate = False, "Not stated — assumed ex-works", "assumed"
        log("Freight", "Vendor did not state freight terms; assumed ex-works (not landed).")
    if "freight" in buyer_inputs:
        landed = buyer_inputs["freight"] == "delivered"
        fnote, fstate = f"Buyer: {buyer_inputs['freight'].replace('_', '-')}", "assumed"
        log("Freight", f"Buyer supplied freight basis: {buyer_inputs['freight']}.")

    # ---------------------------------------------------------------- credit (vendor level)
    cr = ext["credit"]
    kind = cr["kind"]
    net = _num(cr["net_days"]["value"])
    credit_days, credit_state, discount = None, "missing", None
    lead_for_advance = None
    for l in by_line.values():
        d, _ = lead_days(l["lead_time"]["value"])
        if d is not None:
            lead_for_advance = max(lead_for_advance or 0, d)
    history_refs = [h for h in ext.get("history_references", []) if "credit" in h["affects"]]
    if "credit_days" in buyer_inputs:
        credit_days, credit_state = float(buyer_inputs["credit_days"]), "assumed"
        log("Credit", f"Buyer supplied credit terms: {credit_days:g} days ({buyer_inputs.get('credit_note', '')}).")
    elif kind == "net_days" and net is not None:
        credit_days, credit_state = net, _fstate(cr["net_days"])
    elif kind == "discount_then_net" and net is not None:
        credit_days, credit_state = net, "assumed"
        pct, dd = _num(cr["discount_pct"]["value"]), _num(cr["discount_days"]["value"])
        discount = f"{pct:g}% if paid within {dd:g} days" if pct and dd else "early-payment discount offered"
        log("Credit", f"'{cr['text']['value']}' → {net:g} days credit; early-payment discount ({discount}) "
                      "surfaced separately as an opportunity, not netted into price.")
    elif kind == "advance_split":
        a = (_num(cr["advance_pct"]["value"]) or 0) / 100
        if lead_for_advance is not None and a:
            credit_days = round(a * -lead_for_advance + (1 - a) * (net or 0), 1)
            credit_state = "assumed"
            log("Credit", f"'{cr['text']['value']}' → {a:.0%} paid at PO ({lead_for_advance} days before delivery"
                          f"/invoice), balance {net or 0:g} days after delivery → weighted ≈ {credit_days:g} days "
                          "credit (negative = buyer pays before receiving goods).")
        else:
            credit_state = "review"
    elif kind == "lc_at_sight":
        credit_days, credit_state = 0.0, "assumed"
        log("Credit", f"'{cr['text']['value']}' → 0 days: payment on presentation of shipping documents. LC "
                      "opening charges are extra to the buyer.")
    elif kind == "days_from_grn" and net is not None:
        credit_days, credit_state = net, "assumed"
        log("Credit", f"'{cr['text']['value']}' → {net:g} days, assuming GRN (goods receipt) ≈ invoice date. "
                      "GRN-based terms run from receipt, which slightly favours the buyer.")
    elif kind == "refers_to_history" or history_refs:
        awarded = [h for h in history if h["awarded"]]
        others = [h for h in history if not h["awarded"]]
        quote = history_refs[0]["quote"] if history_refs else cr["text"]["value"]
        if awarded:
            last = awarded[-1]
            credit_days = _num(last["terms"].get("credit"))
            credit_state = "assumed"
            log("Credit", f"'{quote}' → resolved from last awarded order {last['ref']} ({last['date']}): "
                          f"'{last['terms'].get('credit')}'.")
        else:
            credit_state = "buyer"
            evidence = ("No purchase order with this vendor exists in our records."
                        + (" The only record is " + "; ".join(
                            f"{h['doc_type'].lower()} {h['ref']} ({h['date']}, {h['material']}, not awarded) stating "
                            f"credit '{h['terms'].get('credit') or '—'}', freight "
                            f"'{h['terms'].get('freight') or 'not recorded'}'" for h in others) + "."
                           if others else " There are no records at all."))
            flags.append({"vendor": code, "kind": "history", "quote": quote,
                          "source": history_refs[0]["source"] if history_refs else cr["text"]["source"],
                          "evidence": evidence, "affects": sorted({a for h in history_refs for a in h["affects"]})
                          or ["credit"]})
            log("Credit", f"'{quote}' — cannot be resolved. {evidence} Not guessed: left blank and sent to the "
                          "buyer.")
    elif kind == "other" and cr["text"]["value"]:
        credit_state = "review"
    if credit_days is None and credit_state == "missing":
        log("Credit", "Credit terms not stated.")

    # ---------------------------------------------------------------- certification per material
    def cert_verdict(material: str, need_by: date) -> tuple[str, str, str]:
        rel = [c for c in ext["cert_assessment"] if c["material"] in (material, "") or len(
            {x["material"] for x in rfq["lines"]}) == 1]
        if not rel:
            return "Not met", "No certification information", "missing"
        parts, verdicts = [], []
        for c in rel:
            vs = c["vendor_standard"]["value"] or "nothing"
            if c["match"] == "exact":
                exp = c["expiry"]["value"]
                try:
                    exp_d = date.fromisoformat(exp) if exp else None
                except ValueError:
                    exp_d = None
                if exp_d and exp_d < need_by:
                    verdicts.append("Needs review")
                    parts.append(f"{c['required']}: expires {exp_d:%d %b %Y}, before need-by {need_by:%d %b %Y}")
                else:
                    verdicts.append("Met")
                    parts.append(f"{c['required']}: ✓ {vs}" + (f" (to {exp})" if exp else " (no expiry stated)"))
            elif c["match"] == "near_equivalent":
                verdicts.append("Needs review")
                parts.append(f"{c['required']}: cites {vs} — near-equivalent, not the same standard")
            else:
                verdicts.append("Not met")
                parts.append(f"{c['required']}: {'not provided' if c['match'] == 'not_provided' else 'cites ' + vs}")
        v = "Not met" if "Not met" in verdicts else "Needs review" if "Needs review" in verdicts else "Met"
        return v, "; ".join(parts), {"Met": "clean", "Needs review": "review", "Not met": "missing"}[v]

    # ---------------------------------------------------------------- rows
    rows = []
    for idx, rl in enumerate(rfq["lines"]):
        need_by = date.fromisoformat(rl["need_by"])
        l = by_line.get(idx)
        cv, cdetail, cstate = cert_verdict(rl["material"], need_by)
        row = {"vendor": code, "vendor_name": vendor["name"], "line": idx, "material": rl["material"],
               "variant": rl["variant"], "qty_required": rl["qty"], "need_by": need_by, "reference": ref,
               "cert": cv, "cert_detail": cdetail, "cert_state": cstate,
               "credit_days": credit_days, "credit_text": cr["text"]["value"], "credit_state": credit_state,
               "discount": discount, "landed": landed, "freight": fnote, "freight_state": fstate,
               "fields": {}, "mapping": None}
        if l is None or not l["quoted"]:
            row.update({"quoted": False, "price_inr": None, "price_original": None, "price_state": "missing",
                        "availability": "Not quoted" + (" (declined)" if l is not None else ""),
                        "avail_state": "missing", "qty_offered": None, "lead_days": None, "delivery_date": None,
                        "delivery": None, "delivery_state": "missing", "row_state": "missing", "confidence": None})
            if l is not None:
                row["fields"]["declined"] = {"value": l["remarks"] or "declined", "source": l["mapping_note"],
                                             "confidence": 1}
            rows.append(row)
            continue
        row["quoted"] = True
        row["mapping"] = f"{l['mapping']}: {l['vendor_item']}"
        for k in ("unit_price", "currency", "units_per_price", "qty_offered", "availability", "lead_time",
                  "lead_time_basis", "balance"):
            row["fields"][k] = l[k]
        # price
        pstate_note = price_notes.get(idx)
        row["price_inr"] = prices_inr.get(idx)
        row["price_original"] = pstate_note[1] if pstate_note else None
        row["price_state"] = _worst(pstate_note[0] if pstate_note else "missing",
                                    _fstate(l["unit_price"]), _fstate(l["currency"]))
        # availability
        units = _num(l["units_per_price"]["value"]) or 1
        offered = _num(l["qty_offered"]["value"])
        av = (l["availability"]["value"] or "").lower()
        if offered is not None:
            q = offered * units
            if units != 1:
                log("Quantity", f"{rl['variant']}: {offered:g} × {units:g} = {q:g} units offered.")
            row["qty_offered"] = q
            if q >= rl["qty"]:
                row["availability"], row["avail_state"] = "Full", _fstate(l["qty_offered"])
            else:
                row["availability"] = f"Partial {q:g}/{rl['qty']} ({q / rl['qty']:.0%})"
                row["avail_state"] = "assumed"
                if l["balance"]["value"]:
                    btxt = l["balance"]["value"]
                    # the lead time that follows the word "balance", not the first one in the sentence
                    i = btxt.lower().rfind("balance")
                    bd, _ = lead_days(btxt[i:] if i >= 0 else btxt)
                    extra = ""
                    if bd:
                        bdate = po_date + timedelta(days=bd)
                        extra = f" → balance by {bdate:%d %b}, {'LATE' if bdate > need_by else 'on time'}"
                    row["availability"] += f"; balance: {l['balance']['value']}{extra}"
        elif av.startswith("full"):
            row["availability"], row["avail_state"] = "Full", _fstate(l["availability"])
        elif av.startswith("not"):
            row["availability"], row["avail_state"] = "Not available", "clean"
        else:
            row["qty_offered"] = rl["qty"]
            row["availability"], row["avail_state"] = "Assumed full (qty not stated)", "assumed"
            log("Availability", f"{rl['variant']}: quantity not stated — assumed full; confirm with vendor.")
        # delivery
        d, note = lead_days(l["lead_time"]["value"])
        basis = (l["lead_time_basis"]["value"] or "").lower()
        basis_assumed = False
        if not basis:
            basis_assumed = True
            basis = "delivered" if landed else "ex_works"
        if d is None:
            row.update({"lead_days": None, "delivery_date": None, "delivery": None, "delivery_state": "missing"})
        else:
            dd = po_date + timedelta(days=d)
            slack = (need_by - dd).days
            row["lead_days"], row["delivery_date"] = d, dd
            if note:
                log("Delivery", f"{rl['variant']}: {note}; PO {po_date:%d %b} + {d} days = {dd:%d %b %Y}.")
            if slack < 0:
                row["delivery"], st_ = f"LATE by {-slack} days ({dd:%d %b})", "clean"
            elif basis.startswith(("ex", "dispatch")):
                row["delivery"], st_ = (f"At risk — {basis.replace('_', '-')} {dd:%d %b}, {slack}d before need-by; "
                                        "transit not included"), "review"
                log("Delivery", f"{rl['variant']}: lead time is {basis.replace('_', '-')}, not arrival at plant — "
                                f"{slack} days of slack must cover transit.")
            else:
                row["delivery"], st_ = f"On time — {dd:%d %b} ({slack}d slack)", "clean"
            row["delivery_state"] = _worst(st_, _fstate(l["lead_time"]), "assumed" if basis_assumed else "clean")
            if basis_assumed:
                log("Delivery", f"{rl['variant']}: lead-time basis not stated; treated as "
                                f"{'delivery to plant (price is landed)' if basis == 'delivered' else 'ex-works (freight not included), so transit is on top'}.")
        confs = [float(l[k]["confidence"]) for k in ("unit_price", "currency", "qty_offered", "lead_time")
                 if l[k]["value"]]
        row["confidence"] = min(confs) if confs else None
        # Row colour = trust in the core numbers. Credit, freight and certification carry their own states.
        row["row_state"] = _worst(row["price_state"], row["avail_state"], row["delivery_state"])
        rows.append(row)

    # volume discount: surfaced, never silently applied
    vd = ext.get("volume_discount") or {}
    if vd.get("text", {}).get("value"):
        t, tc = _num(vd["threshold_amount"]["value"]), (vd["threshold_currency"]["value"] or "INR").upper()
        if t is not None and tc in FX_RATES:
            met = order_value_inr >= t * FX_RATES[tc]
            log("Volume discount", f"'{vd['text']['value']}' — order ≈ INR {order_value_inr:,.0f} vs threshold "
                                   f"{_fmt_money(t, tc)} (≈ INR {t * FX_RATES[tc]:,.0f}): "
                                   f"{'applies — not deducted, shown for negotiation' if met else 'does NOT apply'}.")
    cert_conf = [float(c["vendor_standard"]["confidence"]) for c in ext["cert_assessment"]
                 if c["vendor_standard"]["value"]] or [1.0]
    credit_conf = float(cr["text"]["confidence"] or 0) if cr["text"]["value"] else 1.0
    for row in rows:
        f = row["fields"]

        def c(*keys):
            vals = [float(f[k]["confidence"]) for k in keys if k in f and f[k].get("value")]
            return min(vals) if vals else 1.0
        cert_state = {"Met": "clean", "Needs review": "review", "Not met": "clean"}[row["cert"]]
        row["params"] = {
            "Price": (row["price_state"], c("unit_price", "currency", "units_per_price")),
            "Availability": (row["avail_state"], c("qty_offered", "availability")),
            "Credit period": (row["credit_state"], credit_conf),
            "Quality": (cert_state, min(cert_conf)),
            "Delivery": (row["delivery_state"], c("lead_time", "lead_time_basis")),
            "Reference": ("clean", 1.0),
        }
        row["score"] = param_score(row["params"])
    return {"rows": rows, "ledger": ledger, "flags": flags,
            "vendor_summary": {"vendor": code, "order_value_inr": order_value_inr, "landed": landed,
                               "credit_days": credit_days, "discount": discount}}
