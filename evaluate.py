"""Scoring: the EVAL_SPEC section 4 matching rule and pooled metrics.

Step 2 uses this for the keyword-baseline shortcut audit. Step 3 adds the reviewer call,
JSON parsing, and the full metric set on top.
"""
import re

RULES = ("R1", "R2", "R3", "R4", "R5", "R6")
_TOKEN = re.compile(r"[a-z0-9$%']+")


def tokens(text):
    return _TOKEN.findall(text.lower())


def overlaps(quote, span):
    """Containment either way, or at least half of the shorter span's tokens shared."""
    q, s = quote.strip().lower(), span.strip().lower()
    if not q or not s:
        return False
    if q in s or s in q:
        return True
    tq, ts = set(tokens(q)), set(tokens(s))
    shorter = min(len(tq), len(ts))
    return shorter > 0 and len(tq & ts) >= shorter / 2


def score_case(case, preds):
    """preds: [{rule, quote}]. Returns {tp, fp, fn, tp_rules, fp_rules, fn_rules}.

    Unit is a (case, rule) pair, with predictions deduplicated per pair. A pair is a TP if
    any of its quotes is a verbatim substring of the ad that overlaps a labeled span. A
    labeled rule with no valid quote counts as one FP and one FN.
    """
    text = case["text"]
    labeled = {l["rule"]: l["quotes"] for l in case["labels"]}
    by_rule = {}
    for p in preds:
        by_rule.setdefault(p["rule"], []).append(p.get("quote") or "")
    tp, fp = [], []
    for rule, quotes in by_rule.items():
        ok = rule in labeled and any(
            q and q in text and any(overlaps(q, s) for s in labeled[rule]) for q in quotes)
        (tp if ok else fp).append(rule)
    fn = [r for r in labeled if r not in tp]
    return {"tp": len(tp), "fp": len(fp), "fn": len(fn),
            "tp_rules": tp, "fp_rules": fp, "fn_rules": fn}


def micro(results):
    """Pooled precision, recall, F1 over (case, rule) pairs."""
    tp = sum(r["tp"] for r in results)
    fp = sum(r["fp"] for r in results)
    fn = sum(r["fn"] for r in results)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "micro_f1": f1}
