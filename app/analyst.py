"""AI analyst / challenger over the comparison.

The model never answers from memory. For each question it writes a short pandas program against
the extracted, normalised data; the app runs it; the model then writes the answer from the actual
result. The generated code is shown to the buyer. Arithmetic lives in the executed code.
"""
import math
import re
import statistics
from datetime import date, timedelta

import numpy as np
import pandas as pd

from llm import LLMError, json_call
from master_data import FX_AS_OF, FX_RATES

CODE_SCHEMA = {
    "type": "object",
    "properties": {
        "approach": {"type": "string", "description": "One sentence: how the code answers the question."},
        "code": {"type": "string", "description": "Python (pandas) that assigns the answer to `result`."},
        "chart": {"type": "string", "enum": ["none", "bar"],
                  "description": "bar only if `result` is a DataFrame with a label column and one numeric column."},
    },
    "required": ["approach", "code", "chart"],
    "additionalProperties": False,
}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

SYSTEM = f"""You are a sceptical procurement analyst sitting next to a buyer. Your job is to challenge vendor
responses and the system's recommendation, and to answer questions — always from the data, never from memory.

You get pandas DataFrames:
  quotes  — one row per vendor per RFQ item (columns described below)
  ledger  — the assumptions ledger: vendor, topic, text (every conversion/assumption made)
and a dict FX = {FX_RATES} (INR per unit, as on {FX_AS_OF}), plus pd, np, date, timedelta.

Write Python that computes the answer and assigns it to a variable named `result` (a DataFrame, Series,
number or short string). Rules:
- Do ALL arithmetic in code (totals, differences, FX changes, discounts). Never hard-code numbers you read in the
  sample rows; filter and compute from the frames.
- No imports, no file or network access, no printing. Keep it under ~25 lines.
- Missing values are NaN/None — never treat them as 0 (a missing price is not a free item).
- 'viable' in the recommendation column means not excluded; excluded rows start with '⛔'.
- When challenging, look for: cheaper excluded vendors and whether the exclusion reason is solid, caveats on the
  recommended vendor, single-source items, prices that are not landed, assumptions in the ledger that drive the
  outcome, credit terms that are unresolved, low confidence scores.
- Prefer returning a compact DataFrame with the columns that justify the answer."""

ANSWER_SYSTEM = """You are a sceptical procurement analyst. Write the answer to the buyer's question using ONLY the
computed result given to you. Be direct: lead with the answer, then 2-4 short bullets of evidence or challenge
(risks, caveats, what would change the conclusion). Quote numbers exactly as computed. If the result is empty or
does not answer the question, say so plainly. Plain markdown, no headings, under 150 words."""

FORBIDDEN = re.compile(r"\bimport\b|__|\bopen\s*\(|\bexec\s*\(|\beval\s*\(|\bcompile\s*\(|\bglobals\b|\blocals\b|"
                       r"\bgetattr\b|\bsetattr\b|\bdelattr\b|\bos\.|\bsys\.|subprocess|to_(csv|excel|pickle|parquet)")
SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n)
                 for n in ("abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len", "list", "max",
                       "min", "range", "round", "set", "sorted", "str", "sum", "tuple", "zip", "isinstance",
                       "map", "filter", "reversed", "print")}


def quotes_frame(rows: list[dict], verdicts: dict) -> pd.DataFrame:
    out = []
    for r in rows:
        f = r["fields"]
        v = verdicts.get((r["line"], r["vendor"]), {})
        num = lambda k: (float(re.sub(r"[^0-9.\-]", "", str(f[k]["value"])) or "nan")  # noqa: E731
                         if k in f and f[k].get("value") not in (None, "") and re.search(r"\d", str(f[k]["value"]))
                         else None)
        out.append({
            "vendor": r["vendor"], "vendor_name": r["vendor_name"], "material": r["material"],
            "variant": r["variant"], "item": r["material"] + ("" if r["variant"] == "—" else f" · {r['variant']}"),
            "qty_required": r["qty_required"], "need_by": r["need_by"], "quoted": r["quoted"],
            "price_inr_per_unit": r["price_inr"],
            "total_inr": r["price_inr"] * r["qty_required"] if r["price_inr"] is not None else None,
            "quote_currency": (f.get("currency") or {}).get("value"),
            "quote_unit_price": num("unit_price"), "units_per_price": num("units_per_price") or 1.0,
            "price_as_quoted": r["price_original"], "landed": r["landed"], "freight": r["freight"],
            "availability": r["availability"], "qty_offered": r.get("qty_offered"),
            "lead_days": r["lead_days"], "delivery_date": r["delivery_date"], "delivery": r["delivery"],
            "credit_days": r["credit_days"], "credit_state": r["credit_state"], "early_pay_discount": r["discount"],
            "quality": r["cert"], "quality_detail": r["cert_detail"],
            "referred": r["reference"].startswith("Yes"), "reference": r["reference"],
            "confidence": round(r["score"], 3), "recommendation": v.get("label", ""),
            "recommended": v.get("kind") == "pick", "excluded": v.get("kind") == "excluded",
        })
    return pd.DataFrame(out)


def _run(code: str, quotes: pd.DataFrame, ledger: pd.DataFrame):
    if FORBIDDEN.search(code):
        raise ValueError("generated code uses a disallowed construct")
    env = {"__builtins__": SAFE_BUILTINS, "pd": pd, "np": np, "math": math, "statistics": statistics,
           "date": date, "timedelta": timedelta, "FX": dict(FX_RATES),
           "quotes": quotes.copy(), "ledger": ledger.copy()}
    exec(code, env)  # noqa: S102 — model-written, screened above, restricted builtins, copies of the data
    if "result" not in env:
        raise ValueError("code did not assign `result`")
    return env["result"]


def _preview(result) -> str:
    if isinstance(result, pd.DataFrame):
        return result.head(40).to_string(max_colwidth=80)
    if isinstance(result, pd.Series):
        return result.head(40).to_string()
    return str(result)[:3000]


def ask(question: str, history: list[dict], quotes: pd.DataFrame, ledger: pd.DataFrame, rfq: dict) -> dict:
    """Returns {'answer', 'code', 'approach', 'result', 'chart', 'error'}."""
    schema = "\n".join(f"  {c}: {t}" for c, t in quotes.dtypes.astype(str).items())
    sample = quotes.head(6).to_string(max_colwidth=40)
    ctx = (f"RFQ {rfq['rfq_id']} — {rfq['title']}.\nquotes columns:\n{schema}\n\nquotes sample (first rows of "
           f"{len(quotes)}):\n{sample}\n\nledger: {len(ledger)} rows, columns {list(ledger.columns)}\n")
    prior = "".join(f"\nEarlier Q: {h['q']}\nEarlier answer: {h['a'][:400]}" for h in history[-3:])
    msgs = [{"role": "user", "content": ctx + prior + f"\n\nQuestion: {question}"}]
    plan = json_call(SYSTEM, msgs, CODE_SCHEMA, effort="medium", max_tokens=8000)
    code, err, result = plan["code"], None, None
    try:
        result = _run(code, quotes, ledger)
    except Exception as e:  # noqa: BLE001 — one repair round with the error
        fix = msgs + [{"role": "assistant", "content": code},
                      {"role": "user", "content": f"That code failed: {type(e).__name__}: {e}. Return corrected code."}]
        plan = json_call(SYSTEM, fix, CODE_SCHEMA, effort="medium", max_tokens=8000)
        code = plan["code"]
        try:
            result = _run(code, quotes, ledger)
        except Exception as e2:  # noqa: BLE001
            err = f"{type(e2).__name__}: {e2}"
    if err:
        return {"answer": f"I couldn't compute that: {err}", "code": code, "approach": plan["approach"],
                "result": None, "chart": "none", "error": err}
    ans = json_call(ANSWER_SYSTEM, [{"role": "user", "content": f"Question: {question}\n\nApproach: "
                                     f"{plan['approach']}\n\nComputed result:\n{_preview(result)}"}],
                    ANSWER_SCHEMA, effort="low", max_tokens=2000)
    return {"answer": ans["answer"], "code": code, "approach": plan["approach"], "result": result,
            "chart": plan["chart"], "error": None}


__all__ = ["ask", "quotes_frame", "LLMError"]
