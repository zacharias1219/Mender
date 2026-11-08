"""The gate: pure functions from result lists to a verdict. No API calls, no I/O.

A candidate is admitted only if, checked cheapest first:
  G1 gate micro F1 rises by >= MIN_F1_GAIN and TP pairs by >= MIN_TP_GAIN   else no_f1_gain
  G4 parse failures on the gate split do not rise                         else parse_failures
  G2 no rule's recall drops, pooled train + gate, after confirmation      else rule_recall_drop
  G3 no fully correct case (train + gate) becomes wrong, after confirmation else case_regression
  G5 the winner passes G1 and G4 again on a cache-bypassed gate rerun     else unstable_on_rerun
Results are evaluate.review_case dicts: case_id, parsed, tp_rules, fp, fn, ...
"""
import evaluate as ev

MIN_F1_GAIN = 0.03
MIN_TP_GAIN = 2
EPS = 1e-9
REASONS = ("precheck_failed", "no_f1_gain", "parse_failures", "rule_recall_drop",
           "case_regression", "unstable_on_rerun")


def gate_check(prev_gate, cand_gate):
    """G1 and G4 on the gate split. Returns a reason code, or None if both hold."""
    p, c = ev.micro(prev_gate), ev.micro(cand_gate)
    if c["micro_f1"] - p["micro_f1"] < MIN_F1_GAIN - EPS or c["tp"] - p["tp"] < MIN_TP_GAIN:
        return "no_f1_gain"
    if sum(not r["parsed"] for r in cand_gate) > sum(not r["parsed"] for r in prev_gate):
        return "parse_failures"
    return None


def losses(prev, cand):
    """Case ids (train + gate) where the candidate loses a TP pair or breaks a fully correct case."""
    before = {r["case_id"]: r for r in prev}
    return [r["case_id"] for r in cand
            if set(before[r["case_id"]]["tp_rules"]) - set(r["tp_rules"])
            or (ev.correct(before[r["case_id"]]) and not ev.correct(r))]


def majority(runs):
    """Majority outcome over 3 runs of one case: TP rules in >= 2 runs, correct in >= 2 runs."""
    tp = [rule for rule in ev.RULES if sum(rule in r["tp_rules"] for r in runs) >= 2]
    return {"case_id": runs[0]["case_id"], "tp_rules": tp, "correct": sum(ev.correct(r) for r in runs) >= 2}


def rule_recall(results, cases):
    """{rule: (tp, labeled)} pooled over results. Results may be majority dicts (tp_rules only)."""
    by_id = {c["id"]: c for c in cases}
    out = {}
    for rule in ev.RULES:
        lab = [r for r in results if rule in {l["rule"] for l in by_id[r["case_id"]]["labels"]}]
        out[rule] = (sum(rule in r["tp_rules"] for r in lab), len(lab))
    return out


def confirm(prev, cand, reruns, cases):
    """G2 and G3 after confirmation, pooled train + gate.

    prev, cand: results for the same cases. reruns: {case_id: [rerun1, rerun2]} for every id in
    losses(prev, cand), each a cache-bypassed re-query of the candidate. A lost case is judged by
    its majority over the original and both reruns. Returns a reason code, or None.
    """
    before = {r["case_id"]: r for r in prev}
    adjusted = [majority([r] + reruns[r["case_id"]]) if r["case_id"] in reruns
                else {"case_id": r["case_id"], "tp_rules": r["tp_rules"], "correct": ev.correct(r)}
                for r in cand]
    old, new = rule_recall(prev, cases), rule_recall(adjusted, cases)
    for rule in ev.RULES:
        (pt, pn), (ct, cn) = old[rule], new[rule]
        if pn and ct / cn < pt / pn - EPS:
            return "rule_recall_drop"
    if any(ev.correct(before[a["case_id"]]) and not a["correct"] for a in adjusted):
        return "case_regression"
    return None


def stable(prev_gate, rerun_gate):
    """G5: the winner's cache-bypassed gate rerun must pass G1 and G4 again."""
    return None if gate_check(prev_gate, rerun_gate) is None else "unstable_on_rerun"


# --- Gate self-test (PRD S7): three hand-made bad candidates must all be rejected. ---------------

def _result(case, tp_rules, fp_rules=(), parsed=True):
    labeled = [l["rule"] for l in case["labels"]]
    fn = [r for r in labeled if r not in tp_rules]
    return {"case_id": case["id"], "parsed": parsed, "tp_rules": list(tp_rules),
            "fp_rules": list(fp_rules), "fn_rules": fn, "tp": len(tp_rules),
            "fp": len(fp_rules), "fn": len(fn), "status": {}, "preds": [], "tokens": 0}


def self_test(cases):
    """Run the three bad candidates through the gate against a decent synthetic baseline.

    Baseline: every labeled rule found except R5 and the first R1 case of each split.
    Returns {name: reason}; every reason must be non-None.
    """
    def baseline(c):
        first_r1 = next((x["id"] for x in cases if x["split"] == c["split"]
                         and "R1" in {l["rule"] for l in x["labels"]}), None)
        return [l["rule"] for l in c["labels"] if l["rule"] != "R5" and not (l["rule"] == "R1" and c["id"] == first_r1)]

    prev = [_result(c, baseline(c)) for c in cases]

    def wreck(c):
        # Finds every R5 and R1 (gate F1 rises), but stops finding R2 on train: pooled R2 recall drops.
        found = [l["rule"] for l in c["labels"]]
        return [r for r in found if not (r == "R2" and c["split"] == "train")]

    bad = {
        "flag everything": [_result(c, [l["rule"] for l in c["labels"]],
                                    [r for r in ev.RULES if r not in {l["rule"] for l in c["labels"]}]) for c in cases],
        "flag nothing": [_result(c, []) for c in cases],
        "helps one rule, wrecks another": [_result(c, wreck(c)) for c in cases],
    }
    out = {}
    for name, cand in bad.items():
        pg = [r for r in prev if _split(r, cases) == "gate"]
        cg = [r for r in cand if _split(r, cases) == "gate"]
        reason = gate_check(pg, cg)
        if reason is None:
            lost = set(losses(prev, cand))
            # Confirmation reruns reproduce the same (deterministic) outcome for the self-test.
            reruns = {r["case_id"]: [r, r] for r in cand if r["case_id"] in lost}
            reason = confirm(prev, cand, reruns, cases)
        out[name] = reason
    return out


def _split(result, cases):
    return next(c["split"] for c in cases if c["id"] == result["case_id"])
