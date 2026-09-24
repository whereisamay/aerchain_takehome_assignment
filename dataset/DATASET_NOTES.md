# Dataset notes — vendor responses to RFQ-2026-MCH-005 (Bearing assembly)

> **Answer key. Lives outside `app/`. The application must never read this file or anything
> else in `dataset/` other than vendor documents arriving through the mail inbox.**
> `tests/test_isolation.py` fails the build if any file under `app/` references `dataset`
> or `DATASET_NOTES`.

Regenerate the files with `python dataset/generate_vendor_files.py`.

## The RFQ being answered

| Variant | Qty | Need-by (start of P2) |
|---|---|---|
| Standard | 40 | 2026-11-18 |
| Heavy-duty | 24 | 2026-11-18 |
| High-speed | 20 | 2026-11-18 |

Planned PO date 2026-10-07, so any lead time over **42 days** misses need-by. Required
quality: **ISO 15243 + supplier ISO 9001**. Credit terms basis: days from invoice date.
Price basis: per single bearing assembly.

FX the app should state (not the model): **1 USD = 88.40 INR, 1 EUR = 98.10 INR, as on
22 Sep 2026** (illustrative reference rates).

## Vendor files

| Vendor | File | Format |
|---|---|---|
| A · Shreeji Bearings (Rajkot) | `vendor_A_Shreeji_offer.xlsx` | xlsx, own column order, own part numbers |
| B · Nordlager (Stuttgart) | `vendor_B_Nordlager_Angebot.pdf` | letterhead PDF, part-German, 5.5pt small print |
| C · Kaveri Precision (Coimbatore) | `vendor_C_Kaveri_letter.docx` | Word letter, prose only |
| D · Pacific Motion (Singapore) | `vendor_D_PacificMotion_quote.xlsx` | header at row 7, junk rows, merged cells, formulas |
| E · Vardhman Industrial (Pune) | `vendor_E_Vardhman_email_body.txt` | plain email body, no attachment |
| F · Sri Lakshmi Bearing House (Chennai) | `vendor_F_SriLakshmi_ratecard.pdf` → photograph | generic rate card; `..._photo_SIMULATED.jpg` is a stand-in until a real phone photo replaces it |

## What each vendor actually says, and the correct reading

### A — xlsx, the "clean" referred incumbent
- Part numbers are A's own (`SBT-6212-2RS`, `SBT-NU316-HD`, `SBT-7014-HS`). Rows are in a
  different order from the RFQ (heavy-duty first). Mapping to Standard / Heavy-duty /
  High-speed must come from the descriptions ("general duty", "heavy duty", "high speed")
  and **be flagged as inferred**, not presented as stated.
- INR 2,450 / 4,820 / 6,700 per assembly. FOR Chakan plant, freight included → **landed**.
- **Partial availability:** High-speed 12 of 20 (60%) in 5 weeks, balance 8 in 11 weeks.
  Standard and heavy-duty full quantity.
- Delivery 5 weeks = 35 days → 2026-11-11, before need-by. The balance 8 high-speed at
  11 weeks (77 days → 2026-12-23) is **late**.
- Credit: "45 days net" → 45 days. Matches purchase history (PO-2024-0412, PO-2025-0877).
- Certs: ISO 15243 test cert to 2028-03-31, ISO 9001 (TUV) to 2028-01-14 → **Met**.
- Reference: Yes, Head of Manufacturing, supplied since 2021.

### B — PDF, EUR, near-equivalent standard, buried terms
- EUR 24.50 / 49.00 / 66.00 per unit → INR 2,403.45 / 4,806.90 / 6,474.60 at 98.10.
  Full quantity on all three.
- **Currency trap:** EUR, written in German decimal format (`24,50`, `1.176,00`). A reader that
  treats `1.176,00` as 1.176 is wrong.
- **Terms in 5.5pt small print at the foot of the page:**
  - "2% discount if paid within 10 days of invoice date, otherwise net 30 days" → **30 days
    credit, plus a 2% early-payment discount surfaced separately** as an opportunity.
  - EXW Stuttgart; freight, insurance, customs and duties for buyer's account → **not landed**.
  - 3% volume rebate only for single orders over EUR 5,000. This order is EUR 3,476 →
    **does not apply**. A system that deducts 3% is wrong.
- Delivery "6 weeks after receipt of order, ex works" = 42 days → ready at Stuttgart on
  2026-11-18. That is the need-by date **at the factory gate**, not at the plant. Sea/air
  transit to Pune is additional. Correct behaviour: flag as at risk / needs review.
- **Certification trap (near-equivalent):** DIN 628 and DIN ISO 281, not ISO 15243. Related
  bearing standards, not the one asked for. Must be **Needs review**, never a silent pass.
  ISO 9001 (DQS) valid to 2028-05-31 is fine.
- Reference: Yes, peer buyer at another plant. No supply history.

### C — docx prose letter
- Prices in prose: "Rupees Two Thousand Three Hundred and Eighty (₹2,380)" for Standard,
  ₹4,650 for Heavy-duty. Full quantity on both.
- **Partial: quotes 2 of 3 variants.** High-speed explicitly not offered. Must show as
  *not quoted*, never as ₹0 or a blank that looks like a price.
- Delivery 30 days → 2026-11-06. On time.
- **Credit trap:** "50% advance with PO, balance on delivery" → negative credit. With invoice
  on delivery (day 0) and advance at PO (30 days before), weighted ≈ **−15 days**. The
  assumption must be logged.
- **Freight trap (conditional):** free above ₹5 lakh per despatch. Order value is
  40 × 2,380 + 24 × 4,650 = **₹2,06,800 < ₹5,00,000**, so freight is **at actuals, not
  included**. A system that marks C as "delivered" is wrong.
- **Certification trap (expiry):** ISO 15243 cert valid until **2026-11-12**, which is valid today
  but **expires before the 2026-11-18 need-by**. Must be flagged (Needs review, ask for the renewal).
  ISO 9001 to 2027-08-31 is fine.
- Reference: No.

### D — messy xlsx, USD, per set of 2
- Header row at row 7. Rows 1–6 are title, address, "Q U O T A T I O N", quote no, merged
  address line. Amount and total are **formulas** (`=E8*F8`), so there are no cached values in
  the file. A robust reader recomputes or ignores them. Terms are in merged cells below the table.
- **Unit-basis trap:** UOM "SET (2 PCS)". Qty 20 / 12 / 10 sets = 40 / 24 / 20 pieces (full).
  USD 54 / 108 / 148 per set → USD 27 / 54 / 74 per piece → **INR 2,386.80 / 4,773.60 /
  6,541.60** at 88.40.
- **Currency trap:** USD.
- Credit: "Irrevocable Letter of Credit at sight" → **0 days** credit (paid on presentation of
  shipping documents), plus LC costs to the buyer. Log the assumption.
- **Freight not stated** → assume ex-works Singapore, not landed, and say so.
- Lead time 4 weeks = 28 days → 2026-11-04 ex-works. Plus transit from Singapore, which is tight
  but plausible. Flag, don't assert.
- **Certification trap (product cert, no ISO 9001):** an ISO 15243 test report per lot, but no
  supplier ISO 9001 when both were asked for → **Not met / Needs review** on ISO 9001.
- Reference: No.

### E — two-sentence email, "same terms as our last order" ★ the demo moment
- "std Rs 2300, heavy 4700, high speed 6350 per pc, dispatch in 5 weeks" → INR, full set of
  variants. Quantity not stated ("can supply all three types"), so availability is presumed full
  and **flagged as assumed**.
- Delivery: "dispatch in 5 weeks" → dispatch, not delivery. 35 days → 2026-11-11, OK.
- **"Same terms as our last order":** the buyer's history has **no order with E**. The only
  record is quotation VIS/Q/25-26/0193 (Mar 2025, fastener kits, **not awarded**), which says
  60 days credit, no freight basis. Correct behaviour:
  - do **not** fill credit = 60 or any other number;
  - mark credit terms and freight as **missing / ambiguous**;
  - tell the buyer what was found and what wasn't ("no prior PO; the closest record is an
    unawarded 2025 quotation stating 60 days"), and **ask the buyer**;
  - draft a clarification email to E.
- No certification stated → **missing**, Not met until provided.
- Reference: Yes, the MD, but **no supply history**. The referral must not be read as a track record.

### F — photographed generic rate card
- Not addressed to this RFQ. No RFQ number, no quantities. Includes 8 SKUs, most irrelevant.
  Mapping 6212 → Standard, NU316 → Heavy-duty, 7014 P4 → High-speed must be **inferred and
  flagged** (the series match A's and B's descriptions).
- INR 2,150 / 4,300 / 5,900: **cheapest on every variant**.
- **Delivery trap:** 12–13 weeks = 84–91 days → 2026-12-30 to 2027-01-06, **six-plus weeks after
  need-by**. Cheapest on paper, unusable in practice. Must never be the recommended award
  for this build.
- Credit: "30 days from GRN" → ~30 days, on the assumption that GRN ≈ receipt ≈ invoice date.
  Log that GRN-based terms run from receipt, which slightly favours the buyer.
- Freight: ex-godown Chennai, freight to pay, P&F 1% + GST extra → not landed.
- Availability: not stated → unknown / assumed.
- Certs: ISO 9001 to 2028-06-30, ISO 15243 to 2027-12-31 → Met.
- Minimum order value ₹25,000 (not binding here).

## Trap checklist → where it lives

| # | Trap | Vendor(s) | Correct behaviour |
|---|---|---|---|
| 1a | Quotes 2 of 3 variants | C | High-speed shown as *not quoted*, never ₹0; follow-up drafted |
| 1b | Full on one variant, 60% on another | A | High-speed 12/20 now, 8 at 11 weeks (late) |
| 2 | Currency | B (EUR), D (USD) | Converted at a stated rate and date in Python; the model never supplies a rate |
| 3 | Unit basis | D (per set of 2) | Halved to per-piece, logged in the ledger |
| 4a | Near-equivalent standard | B (DIN 628) | Needs review, not Met |
| 4b | Cert expires before need-by | C (2026-11-12) | Needs review; ask for renewal |
| 4c | Product cert but no ISO 9001 | D | ISO 9001 not met |
| 5 | Five credit-term shapes | A 45 net · B 2/10 net 30 · C 50% adv · D LC at sight · F 30 from GRN | 45 / 30 (+2% discount flagged) / ≈ −15 / 0 / ≈ 30, each with a logged assumption |
| 6 | Cheapest but late | F (12–13 wks) | Excluded as unusable for this need-by |
| 7 | Freight | A delivered · B EXW · C conditional (not met) · D unstated · E unstated · F ex-godown | Landed or not, with the assumption stated |
| 8 | Genuine ambiguity | E "same terms as our last order" | Flag, show partial history, **ask the buyer**; never guess |

### Secondary traps
- German number format (B), prices written in words (C), Excel formulas with no cached values (D).
- Vendor part numbers and descriptions that never say "Standard" or "Heavy-duty" (A, B, D, F).
- Ex-works "delivery" dates that are not arrival dates (B, D).
- Volume rebate whose threshold isn't met (B).
- A "dispatch" date rather than a delivery date (E).
- A referral with no supply history (E) vs one with history (A).

## Correct-answer sketch for the demo questions (for the presenter only)
- *Cheapest per variant, certification Met only:* A and F are the only fully Met vendors.
  F is cheapest on paper but late. Among on-time, Met vendors, A wins every variant.
- *Which vendors can deliver before the P2 need-by?* A (except the 8 high-speed balance),
  C, D (ex-works, transit risk), E. B is borderline (ex-works on the day). F is late.
- *Variants with only one viable quote* depend on how strictly "viable" is read. With
  Met + on time + landed, High-speed is thin: A can do only 12 of 20 on time, and C doesn't
  quote it.

## Any other RFQ: the vendor simulator (`vendor_sim/`)

The hand-built files above answer **RFQ-2026-MCH-005 only**. When the buyer sends any other RFQ
(e.g. all P1 castings), "Simulate vendor replies arriving" runs `vendor_sim/simulate.py`, which
writes real files for exactly the items on that RFQ, from the same six vendors, each with a
consistent character. It gives a deliberate mix of strong and weak quotes:

| Vendor | Format | Character → expected reading |
|---|---|---|
| A | xlsx, own part codes, reversed order | INR, delivered, 45 days, all standards met, ~5% dearer, lead ≈ 0.85 × typical. Tight windows (≤ 7 days, e.g. P1 under the default plan): **ex-stock** → strong, high confidence |
| B | letterhead PDF, EUR (German number format) | ex-works, 2/10 net 30 in 5.5pt print, cites **DIN/EN near-equivalents** → Needs review |
| C | docx prose | **declines the last item**, 50% advance (≈ negative credit), free freight above ₹5 lakh (met or not depending on order value), first certificate **expires 6 days before need-by** → Needs review. Ex-stock on tight windows |
| D | messy xlsx, header row 7 | USD; parts under ₹20k priced **per pack of 2**; LC at sight (0 days); no freight terms; test reports only, **no ISO 9001 / CE** → Not met |
| E | two-sentence email | prices only, "dispatch in N weeks", **"same terms as our last order"** → buyer decision; no certs → Not met |
| F | photographed rate card, from personal webmail (→ Unmatched) | **cheapest (~12% under market)** but 12–13 weeks → late except where need-by is far out (P4 under the default plan) |

The simulator is the vendors' side of the conversation. The app's extraction and normalisation never
import it (`tests/test_isolation.py`); they only read the files it writes.

**Planning note:** under the default plan (target 10 Feb 2027) P1 materials are needed on the planned
PO date itself (week 0 of the build). The RFQ Generator warns about this; only ex-stock offers can
be on time.
