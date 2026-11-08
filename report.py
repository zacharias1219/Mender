"""REPORT.md and report.svg from a run folder's logged numbers; --criteria prints PRD S1 to S12.

Usage: python report.py runs/<timestamp> [--criteria]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

import evaluate as ev
import gate
import gen_data as gd

RULES = ev.RULES


def load(run_dir):
    with open(os.path.join(run_dir, "run.json"), encoding="utf-8") as f:
        st = json.load(f)
    cands = []
    path = os.path.join(run_dir, "candidates.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cands = [json.loads(line) for line in f if line.strip()]
    return st, cands


def val(metrics, key):
    return metrics[key][0]


def criteria(st, cands):
    """[(id, name, passed, measured)] for S1 to S12. Every row has a value; missing data is a FAIL."""
    rows = []
    acc = st.get("accepted", [])
    first = acc[0] if acc else None
    rows.append(("S1", "accepted patch", bool(acc), f"{len(acc)} approved"))
    rows.append(("S1a", "it raises train micro F1", bool(first) and first["train_f1_delta"] > 0,
                 f"{first['train_f1_delta']:+.3f}" if first else "no patch"))
    rows.append(("S1b", "it raises gate F1 >= +0.030 and TP >= +2", bool(first) and
                 first["gate_f1_delta"] >= gate.MIN_F1_GAIN - gate.EPS and first["gate_tp_delta"] >= gate.MIN_TP_GAIN,
                 f"{first['gate_f1_delta']:+.3f}, {first['gate_tp_delta']:+d} pairs" if first else "no patch"))
    rows.append(("S1c", "no confirmed rule drop or case regression", bool(first),
                 "0 confirmed (enforced by gate.confirm)" if first else "no patch"))
    coded = [c for c in cands if c.get("reason") in gate.REASONS]
    rows.append(("S2", "a rejected candidate with a logged reason code", bool(coded),
                 ", ".join(f"{k} {v}" for k, v in sorted(Counter(c["reason"] for c in coded).items())) or "none"))
    t = st.get("test")
    if t:
        b, f = t["v000"]["metrics"], t["final"]["metrics"]
        improved = sum(not t["v000"]["correct"][k] and t["final"]["correct"][k] for k in t["v000"]["correct"])
        worsened = sum(t["v000"]["correct"][k] and not t["final"]["correct"][k] for k in t["v000"]["correct"])
        d = val(f, "micro_f1") - val(b, "micro_f1")
        rows.append(("S3", "test micro F1 >= baseline + 0.050, more cases improved than worsened",
                     d >= 0.05 - gate.EPS and improved > worsened,
                     f"{val(b, 'micro_f1'):.3f} -> {val(f, 'micro_f1'):.3f} ({d:+.3f}); improved {improved}, worsened {worsened}"))
        d = val(f, "case_accuracy") - val(b, "case_accuracy")
        rows.append(("S4", "test case accuracy >= baseline + 0.050", d >= 0.05 - gate.EPS,
                     f"{val(b, 'case_accuracy'):.3f} -> {val(f, 'case_accuracy'):.3f} ({d:+.3f})"))
        low = [r for r in RULES if b[f"rule_recall[{r}]"][2] and val(f, f"rule_recall[{r}]") < val(b, f"rule_recall[{r}]") - gate.EPS]
        rows.append(("S5", "no test rule recall below baseline", not low, "below: " + ", ".join(low) if low else "none below"))
        pb, pf = val(b, "parse_failure_rate"), val(f, "parse_failure_rate")
        rows.append(("S6", "parse failures, final <= baseline and <= 5%", pf <= pb + gate.EPS and pf <= 0.05,
                     f"{pb:.3f} -> {pf:.3f}"))
    else:
        for sid, name in (("S3", "test micro F1"), ("S4", "test case accuracy"), ("S5", "test rule recall"),
                          ("S6", "parse failures")):
            rows.append((sid, name, False, "test not scored (pilot run)"))
    cases = gd.read_jsonl(gd.DATASET)
    st7 = gate.self_test([c for c in cases if c["split"] != "test"])
    rows.append(("S7", "gate self-test: 3 bad candidates rejected", all(st7.values()),
                 f"{sum(bool(v) for v in st7.values())}/3 ({', '.join(f'{k}: {v}' for k, v in st7.items())})"))
    rp = st.get("reproducibility")
    rows.append(("S8", "re-run from cache: score delta 0.000", bool(rp) and abs(rp["gate_f1_delta"]) < gate.EPS
                 and rp["network_calls"] == 0,
                 f"delta {rp['gate_f1_delta']:.3f}, {rp['network_calls']} network calls" if rp else "not measured"))
    n = None
    if os.path.exists(gd.SPOTCHECK):
        m = re.search(r"^Result:\s*(\d+)\s*/\s*15", open(gd.SPOTCHECK, encoding="utf-8").read(), re.M)
        n = int(m.group(1)) if m else None
    rows.append(("S9", "spot-check >= 14 of 15", n is not None and n >= 14, f"{n}/15" if n is not None else "not recorded"))
    checks = dict((k, ok) for k, ok, _ in gd.validate(cases))
    kb = gd.keyword_baseline(cases)
    shape = len(cases) == 90 and sum(not c["labels"] for c in cases) == 26 and checks["C1"]
    rows.append(("S10", "90 ads, 26 clean, rule floors, keyword F1 < 0.70 on gate and test",
                 shape and kb["gate"]["micro_f1"] < 0.70 and kb["test"]["micro_f1"] < 0.70,
                 f"{len(cases)} ads, {sum(not c['labels'] for c in cases)} clean, floors {'met' if checks['C1'] else 'NOT met'}, "
                 f"keyword gate {kb['gate']['micro_f1']:.3f} test {kb['test']['micro_f1']:.3f}"))
    rows.append(("S11", "full run on the free tier, 0 unrecovered API failures",
                 st.get("completed", False) and st.get("stop_reason") != "aborted_api",
                 f"completed {st.get('completed')}, {st.get('api_failures', 0)} exhausted calls, all recovered"
                 if st.get("completed") else f"not completed ({st.get('stop_reason')})"))
    if t:
        b, f = t["v000"]["metrics"], t["final"]["metrics"]
        ok = val(f, "clean_fpr") <= val(b, "clean_fpr") + gate.EPS and val(f, "adversarial_recall") >= val(b, "adversarial_recall") - gate.EPS
        rows.append(("S12", "test clean FPR <= baseline, adversarial recall >= baseline", ok,
                     f"FPR {val(b, 'clean_fpr'):.3f} -> {val(f, 'clean_fpr'):.3f}; adversarial recall "
                     f"{val(b, 'adversarial_recall'):.3f} -> {val(f, 'adversarial_recall'):.3f}"))
    else:
        rows.append(("S12", "test clean FPR and adversarial recall", False, "test not scored (pilot run)"))
    return rows


def bar(x, width=20):
    n = round(x * width)
    return "#" * n + "." * (width - n)


def svg(st):
    """Gate micro F1 by round (line) and test per-rule recall before/after (bars). Standard library only."""
    W, H = 720, 300
    hist = st["history"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="11">',
           f'<rect width="{W}" height="{H}" fill="white"/>',
           '<text x="20" y="20" font-size="13">Gate micro F1 by round</text>',
           '<text x="380" y="20" font-size="13">Test per-rule recall: v000 (grey) vs final (blue)</text>']
    x0, y0, w, h = 40, 40, 300, 200
    out.append(f'<line x1="{x0}" y1="{y0 + h}" x2="{x0 + w}" y2="{y0 + h}" stroke="#999"/>')
    out.append(f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0 + h}" stroke="#999"/>')
    for v in (0, 0.5, 1):
        out.append(f'<text x="{x0 - 28}" y="{y0 + h - v * h + 4}">{v:.1f}</text>')
    step = w / max(1, st["rounds"])
    pts = [(x0 + hh["round"] * step, y0 + h - hh["gate_f1"] * h) for hh in hist]
    out.append('<polyline fill="none" stroke="#1f6feb" stroke-width="2" points="'
               + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + '"/>')
    for (x, y), hh in zip(pts, hist):
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#1f6feb"/>')
        out.append(f'<text x="{x - 10:.1f}" y="{y - 8:.1f}">{hh["gate_f1"]:.3f}</text>')
        out.append(f'<text x="{x - 4:.1f}" y="{y0 + h + 14}">{hh["round"]}</text>')
    t = st.get("test")
    x1 = 390
    for i, r in enumerate(RULES):
        bx = x1 + i * 50
        for j, key in enumerate(("v000", "final")):
            v = t[key]["metrics"][f"rule_recall[{r}]"][0] if t else 0
            out.append(f'<rect x="{bx + j * 18}" y="{y0 + h - v * h:.1f}" width="16" height="{v * h:.1f}" '
                       f'fill="{"#bbb" if j == 0 else "#1f6feb"}"/>')
        out.append(f'<text x="{bx + 8}" y="{y0 + h + 14}">{r}</text>')
    if not t:
        out.append(f'<text x="{x1}" y="{y0 + 100}">test not scored (pilot run)</text>')
    out.append("</svg>")
    return "\n".join(out)


def markdown(st, cands, rows):
    L = [f"# Self-repair run {st['run']}", ""]
    if st.get("reuse_test"):
        L += ["> **--reuse-test:** this run scored the test split again for a prompt already in the "
              "ledger. Its test numbers are not a first, blind measurement.", ""]
    L += [f"Dataset sha256 `{st['dataset_sha256']}`. Budget {st['rounds']} rounds x {st['candidates']} "
          f"candidates. Stop reason: `{st['stop_reason']}`. Final prompt: `{st.get('final_version')}`.", "",
          "![report](report.svg)", "", "## Success criteria (PRD section 6)", "", "| # | Criterion | Result | Measured |",
          "|---|---|---|---|"]
    L += [f"| {i} | {n} | {'PASS' if ok else 'FAIL'} | {m} |" for i, n, ok, m in rows]
    t = st.get("test")
    for title, key in (("Test (held out, scored once per prompt)", "test"), ("Gate", "gate"), ("Train", "train")):
        if key == "test":
            if not t:
                L += ["", f"## {title}", "", "Not scored: pilot run (`--skip-test`)."]
                continue
            b, f = t["v000"]["metrics"], t["final"]["metrics"]
        else:
            b, f = st["baseline"][key], st["final"][key]
        L += ["", f"## {title}: v000 vs {st.get('final_version')}", "", "| Metric | v000 | final |", "|---|---|---|"]
        for k in b:
            if k == "tokens_per_call":
                continue
            fmt = lambda m: f"{m[0]:.3f} ({m[1]}/{m[2]})" if k != "micro_f1" else f"{m[0]:.3f} (tp {m[1]}, {m[2]})"  # noqa: E731
            L.append(f"| {k} | {fmt(b[k])} | {fmt(f[k])} |")
        if key == "test":
            L += ["", "Per-rule recall bars (v000, then final):", "", "```"]
            L += [f"{r}  {bar(b[f'rule_recall[{r}]'][0])} {b[f'rule_recall[{r}]'][0]:.3f}   "
                  f"{bar(f[f'rule_recall[{r}]'][0])} {f[f'rule_recall[{r}]'][0]:.3f}" for r in RULES]
            L.append("```")
    L += ["", "## Accepted patches", ""]
    for x in st["accepted"]:
        L += [f"### {x['version']} (round {x['round']}, section `{x['section']}`): gate F1 {x['gate_f1_delta']:+.3f}, "
              f"{x['gate_tp_delta']:+d} TP pairs, train F1 {x['train_f1_delta']:+.3f}", "", "```diff"]
        L += ["- " + line for line in x["old"].split("\n")] + ["+ " + line for line in x["new"].split("\n")] + ["```", ""]
    if not st["accepted"]:
        L.append("None.")
    L += ["", "## Candidates and verdicts", "", "| Round | Cand | Section | Verdict | Gate dF1 | Train dF1 |", "|---|---|---|---|---|---|"]
    for c in cands:
        v = "APPROVED" if c["approved"] else (c["reason"] or "passed, not selected")
        f1 = lambda x: "" if x is None else f"{x:+.3f}"  # noqa: E731
        L.append(f"| {c['round']} | {c['cand']} | {c['section'] or '-'} | {v} | {f1(c['gate_f1_delta'])} | {f1(c['train_f1_delta'])} |")
    L += ["", "Rejections by reason code: " + (", ".join(f"`{k}` {v}" for k, v in sorted(
        Counter(c["reason"] for c in cands if c["reason"]).items())) or "none"), ""]
    return "\n".join(L)


def write(run_dir):
    st, cands = load(run_dir)
    rows = criteria(st, cands)
    with open(os.path.join(run_dir, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write(markdown(st, cands, rows))
    with open(os.path.join(run_dir, "report.svg"), "w", encoding="utf-8") as f:
        f.write(svg(st))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Write REPORT.md and report.svg for a run")
    ap.add_argument("run_dir")
    ap.add_argument("--criteria", action="store_true", help="print S1 to S12 as PASS or FAIL")
    a = ap.parse_args()
    rows = write(a.run_dir)
    if a.criteria:
        for i, n, ok, m in rows:
            print(f"{i:<4} {'PASS' if ok else 'FAIL'}  {n}: {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
