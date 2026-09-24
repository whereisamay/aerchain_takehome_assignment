"""Which vendor suits which item — ranked in plain Python by a stated rule, explained from the facts.

Rule, per item (single-material RFQ: per variant; multi-material RFQ: per material):
  1. Exclude a vendor that did not quote, is LATE against need-by, fails a required standard, or has no stock.
  2. Rank the rest: full quantity before partial; then fewest open issues (standard needing review,
     ex-works/at-risk delivery, unresolved credit terms, price not landed); then lowest total cost.
The model plays no part in ranking; it only extracted the facts being ranked.
"""

HARD = "excluded"


def _issues(r: dict) -> tuple[list[str], list[str]]:
    hard, soft = [], []
    if not r["quoted"] or r["price_inr"] is None:
        hard.append("did not quote" if not r["quoted"] else "no usable price")
    if r["delivery"] and r["delivery"].startswith("LATE"):
        hard.append(r["delivery"].replace("LATE", "late", 1))
    if r["cert"] == "Not met":
        hard.append(f"quality standard not met ({r['cert_detail']})")
    if r["availability"].startswith("Not available"):
        hard.append("not available")
    if r["cert"] == "Needs review":
        soft.append(f"standard needs review ({r['cert_detail']})")
    if r["quoted"] and r["lead_days"] is None:
        soft.append("lead time not stated")
    if r["delivery_state"] == "review":
        soft.append("delivery at risk (" + (r["delivery"] or "").split("—")[-1].strip() + ")")
    if r["credit_state"] in ("buyer", "missing"):
        soft.append("credit terms unresolved")
    if r["landed"] is False:
        soft.append("price not landed (freight extra)")
    return hard, soft


def _group_label(rows: list[dict], single: bool) -> str:
    r = rows[0]
    return r["variant"] if single else r["material"]


def recommend(rows: list[dict], rfq: dict) -> list[dict]:
    materials = list(dict.fromkeys(l["material"] for l in rfq["lines"]))
    single = len(materials) == 1
    if single:
        groups = {f"{l['variant']}": [i] for i, l in enumerate(rfq["lines"])}
    else:
        groups = {m: [i for i, l in enumerate(rfq["lines"]) if l["material"] == m] for m in materials}

    out = []
    for label, idxs in groups.items():
        by_vendor: dict[str, list[dict]] = {}
        for r in rows:
            if r["line"] in idxs:
                by_vendor.setdefault(r["vendor"], []).append(r)
        cands = []
        for v, rs in by_vendor.items():
            hard, soft = [], []
            for r in rs:
                h, s = _issues(r)
                hard += [x for x in h if x not in hard]
                soft += [x for x in s if x not in soft]
            if len(rs) < len(idxs):
                hard.append("did not quote every variant")
            partial = any(r["availability"].startswith("Partial") or r["availability"].startswith("Assumed")
                          for r in rs)
            total = sum(r["qty_required"] * r["price_inr"] for r in rs if r["price_inr"] is not None)
            cands.append({"vendor": v, "name": rs[0]["vendor_name"], "rows": rs, "hard": hard, "soft": soft,
                          "partial": partial, "total": total, "credit": rs[0]["credit_days"],
                          "reference": rs[0]["reference"], "score": min(r["score"] for r in rs)})
        viable = sorted([c for c in cands if not c["hard"]],
                        key=lambda c: (c["partial"], len(c["soft"]), c["total"]))
        excluded = sorted([c for c in cands if c["hard"]], key=lambda c: c["total"] or 1e18)
        rec = {"item": label, "rows_idx": idxs, "viable": viable, "excluded": excluded, "pick": None}
        if viable:
            p = viable[0]
            rec["pick"] = p
            rec["status"] = "Recommended" if not p["soft"] and not p["partial"] else "Best available — with caveats"
            rec["why"] = _why(p, viable, excluded, single)
        else:
            rec["status"] = "No viable quote"
            rec["why"] = ["Every vendor is excluded: " + "; ".join(f"{c['vendor']} — {c['hard'][0]}"
                                                                    for c in excluded)]
        out.append(rec)
    return out


def _why(p: dict, viable: list[dict], excluded: list[dict], single: bool) -> list[str]:
    r0 = p["rows"][0]
    unit = (f"₹{r0['price_inr']:,.2f}/unit" if single else f"₹{p['total']:,.0f} for all variants")
    cheapest_viable = min(viable, key=lambda c: c["total"])
    lines = []
    if p is cheapest_viable:
        lines.append(f"Lowest cost among vendors that are compliant and on time: {unit} "
                     f"(₹{p['total']:,.0f} for the required quantity).")
    else:
        d = p["total"] - cheapest_viable["total"]
        lines.append(f"{unit} (₹{p['total']:,.0f} total) — ₹{d:,.0f} more than {cheapest_viable['vendor']}, which "
                     f"has more open issues: {'; '.join(cheapest_viable['soft']) or 'partial quantity'}.")
    cert = r0["cert"]
    lines.append(f"Quality: {cert}" + (f" — {r0['cert_detail']}" if cert != "Met" else ""))
    lines.append(f"Delivery: {r0['lead_days']} days → {r0['delivery']}" if r0["lead_days"] is not None
                 else "Delivery: not stated")
    avail = "; ".join(sorted({r["availability"] for r in p["rows"]}))
    lines.append(f"Availability: {avail}")
    cd = p["credit"]
    lines.append("Credit: " + ("unresolved" if cd is None else f"{cd:g} days") +
                 (f" (+ {r0['discount']})" if r0["discount"] else ""))
    lines.append(f"Reference: {p['reference']}")
    if p["soft"]:
        lines.append("Caveats: " + "; ".join(p["soft"]))
    for c in viable[1:]:
        if c["partial"] and not c["soft"]:
            avail = "; ".join(sorted({r["availability"].split(";")[0] for r in c["rows"]}))
            lines.append(f"Alternative: {c['vendor']} has no open issues but only partial quantity ({avail}) — "
                         "consider splitting the award.")
    cheaper_excl = [c for c in excluded if c["total"] and c["total"] < p["total"]]
    for c in cheaper_excl:
        lines.append(f"Not {c['vendor']} (₹{c['total']:,.0f}, cheaper): {c['hard'][0]}")
    return lines


def verdicts(rows: list[dict], rfq: dict) -> dict[tuple[int, str], dict]:
    """Per (item index, vendor): a short verdict for the comparison table, plus a sort rank.
    Single-material RFQ: judged per variant. Multi-material: per material (all its variants together)."""
    out = {}
    for rec in recommend(rows, rfq):
        ranked = rec["viable"] + rec["excluded"]
        for rank, c in enumerate(ranked):
            if c is rec["pick"]:
                label = "✅ Recommended" if not c["soft"] and not c["partial"] else \
                    "✅ Best available — " + (c["soft"][0] if c["soft"] else "partial quantity")
                kind = "pick"
            elif not c["hard"]:
                d = c["total"] - rec["pick"]["total"] if rec["pick"] else 0
                bits = [f"₹{abs(d):,.0f} {'dearer' if d >= 0 else 'cheaper'}"] if rec["pick"] else []
                if c["partial"]:
                    bits.append("partial quantity")
                bits += c["soft"][:1]
                label = f"Viable #{rank + 1} — " + "; ".join(bits)
                kind = "viable"
            else:
                label = "⛔ " + c["hard"][0][0].upper() + c["hard"][0][1:]
                kind = "excluded"
            for r in c["rows"]:
                out[(r["line"], r["vendor"])] = {"label": label, "kind": kind, "rank": rank}
    return out
