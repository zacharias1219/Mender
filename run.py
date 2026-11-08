"""CLI for the self-repairing eval harness.

  python run.py gen-data [--validate | --sample [--seed N] | --freeze]
  python run.py eval --split {train,gate,test} --prompt prompts/vNNN.json
  python run.py run [--skip-test | --reuse-test]
"""
import argparse
import datetime
import hashlib
import json
import os
import sys
import time

import evaluate as ev
import gate
import gen_data as gd
import groq_client as gc
import repair

ROUNDS = 3        # LIMITS.md: Richard's one-proof budget (spec default 8)
CANDIDATES = 2    # LIMITS.md: (spec default 3)
MAX_RERUNS = 3    # stability reruns tried per round, best passer first
STOP_AFTER = 3    # consecutive all-rejected rounds
LEDGER = os.path.join(gd.DATA, "test_ledger.json")
API_RETRY_WAIT = 60


def check_dataset():
    """G9: abort before any API call unless data/dataset.jsonl matches its recorded sha256."""
    if not (os.path.exists(gd.DATASET) and os.path.exists(gd.HASHFILE)):
        sys.exit("abort: no frozen dataset (run: python run.py gen-data --freeze)")
    want, got = gd.recorded_hash(), gd.sha256(gd.DATASET)
    if want != got:
        sys.exit(f"abort: dataset hash mismatch, recorded {want[:12]}..., file {got[:12]}... "
                 "No API call was made.")
    return got


def report_validation(cases):
    print(gd.coverage(cases))
    results = gd.validate(cases)
    for name, ok, detail in results:
        print(f"{name:<6} {'PASS' if ok else 'FAIL'}  {detail}")
    return all(ok for _, ok, _ in results)


def print_baseline(cases):
    ok = True
    for split, m in gd.keyword_baseline(cases).items():
        flag = "" if split == "train" else ("  PASS (< 0.70)" if m["micro_f1"] < 0.70 else "  FAIL (>= 0.70)")
        ok &= split == "train" or m["micro_f1"] < 0.70
        print(f"keyword baseline {split:<5} micro F1 {m['micro_f1']:.3f} "
              f"(tp {m['tp']}, fp {m['fp']}, fn {m['fn']}){flag}")
    return ok


def cmd_gen_data(a):
    if a.sample or a.freeze:
        if not os.path.exists(gd.DRAFT):
            sys.exit("no data/draft.jsonl: run python run.py gen-data --validate first")
        cases, digest = gd.read_jsonl(gd.DRAFT), gd.sha256(gd.DRAFT)
        if a.sample:
            gd.write_spotcheck(gd.sample(cases, a.seed), a.seed, digest)
            print(f"wrote data/spotcheck.md: 15 ads from draft {digest[:12]}..., seed {a.seed}")
            return 0
        if not report_validation(cases):
            sys.exit("freeze refused: a constraint fails")
        passed, why = gd.spotcheck_passed(digest)
        if not passed:
            sys.exit(f"freeze refused: {why}")
        if not print_baseline(cases):
            sys.exit("freeze refused: keyword baseline >= 0.70, regenerate harder ads")
        print(f"{why}; frozen data/dataset.jsonl sha256 {gd.freeze()}")
        return 0
    cases, failures = gd.generate()
    print(f"network calls: {gc.network_calls}")
    ok = report_validation(cases)
    print_baseline(cases)
    return 0 if ok and not failures else 1


def cmd_eval(a):
    digest = check_dataset()
    if a.split == "test":
        sys.exit("refused: the test split is scored only inside `python run.py run` (G7)")
    prompt = ev.load_prompt(a.prompt)
    cases = [c for c in gd.read_jsonl(gd.DATASET) if c["split"] == a.split]
    results = ev.evaluate_split(prompt, cases)
    out = os.path.join(gd.ROOT, "runs", "eval", f"{prompt['version']}_{a.split}.jsonl")
    ev.write_results(out, results)
    print(f"dataset {digest[:12]}  prompt {prompt['version']}  split {a.split}  ads {len(cases)}")
    print(ev.format_metrics(ev.metrics(results, cases)))
    top = ev.clusters(results)
    for (rule, kind), ids in top[:3]:
        print(f"cluster {rule} {kind:<11} {len(ids)} cases: {' '.join(ids)}")
    print(f"results: {os.path.relpath(out, gd.ROOT)}\nnetwork calls: {gc.network_calls}")
    return 0


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def prompt_hash(prompt):
    return hashlib.sha256(json.dumps(prompt["sections"], sort_keys=True).encode()).hexdigest()


class Run:
    """State of one `run`, saved to runs/<ts>/run.json after every step."""

    def __init__(self, digest, skip_test, reuse_test):
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.dir = os.path.join(gd.ROOT, "runs", ts)
        os.makedirs(os.path.join(self.dir, "prompts"))
        self.rerun_cache = os.path.join(self.dir, "rerun_cache")  # bypass reruns never touch the main cache
        self.state = {"run": ts, "dataset_sha256": digest, "rounds": ROUNDS, "candidates": CANDIDATES,
                      "skip_test": skip_test, "reuse_test": reuse_test, "history": [], "accepted": [],
                      "stop_reason": None, "api_failures": 0, "completed": False}
        self.exhausted = 0

    def save(self):
        save_json(os.path.join(self.dir, "run.json"), self.state)

    def log(self, record):
        with open(os.path.join(self.dir, "candidates.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def guarded(self, fn, *args, **kw):
        """Abort rule 4: 3 consecutive calls that exhaust their retries abort the run (resumable from cache)."""
        while True:
            try:
                out = fn(*args, **kw)
                self.exhausted = 0
                return out
            except gc.GroqBadOutput:
                raise
            except gc.GroqError as e:
                self.exhausted += 1
                self.state["api_failures"] += 1
                print(f"API call exhausted its retries ({self.exhausted}/3): {e}")
                if self.exhausted >= 3:
                    self.state["stop_reason"] = "aborted_api"
                    self.save()
                    sys.exit(f"abort: 3 consecutive API calls exhausted their retries. State is in "
                             f"{os.path.relpath(self.dir, gd.ROOT)}; rerun `python run.py run` to resume from cache.")
                time.sleep(API_RETRY_WAIT)


def summary(results, cases):
    m = ev.metrics(results, cases)
    return {k: list(v) for k, v in m.items()}


def cmd_run(a):
    digest = check_dataset()
    cases = gd.read_jsonl(gd.DATASET)
    split = {s: [c for c in cases if c["split"] == s] for s in gd.SPLITS}
    by_id = {c["id"]: c for c in cases}
    ads = [c["text"] for c in cases]
    R = Run(digest, a.skip_test, a.reuse_test)
    st = R.state
    base = ev.load_prompt(os.path.join(gd.ROOT, "prompts", "v000.json"))
    save_json(os.path.join(R.dir, "prompts", "v000.json"), base)
    cur = base
    cur_tr = R.guarded(ev.evaluate_split, cur, split["train"])
    cur_ga = R.guarded(ev.evaluate_split, cur, split["gate"])
    st["baseline"] = {"train": summary(cur_tr, split["train"]), "gate": summary(cur_ga, split["gate"])}
    st["history"].append({"round": 0, "version": "v000", "gate_f1": ev.micro(cur_ga)["micro_f1"],
                          "train_f1": ev.micro(cur_tr)["micro_f1"]})
    R.save()
    print(f"run {st['run']}  dataset {digest[:12]}  v000 gate F1 {ev.micro(cur_ga)['micro_f1']:.3f} "
          f"train F1 {ev.micro(cur_tr)['micro_f1']:.3f}")
    rejections, streak = [], 0
    for rnd in range(1, ROUNDS + 1):
        cluster, cands = R.guarded(repair.propose, cur, base, cur_tr, split["train"], rejections, CANDIDATES, ads)
        passers, records = [], []
        version = f"v{len(st['accepted']) + 1:03d}"
        for k, (patch, why) in enumerate(cands, 1):
            rec = {"round": rnd, "cand": k, "cluster": cluster, "section": patch and patch["section"],
                   "new_text": patch and patch["new_text"], "rationale": patch and patch["rationale"],
                   "approved": False, "reason": None, "detail": why,
                   "gate_f1_delta": None, "gate_tp_delta": None, "train_f1_delta": None}
            records.append(rec)
            if why:
                rec["reason"] = "precheck_failed"
                continue
            cp = repair.apply(cur, patch, version)
            cg = R.guarded(ev.evaluate_split, cp, split["gate"])
            rec["gate_f1_delta"] = ev.micro(cg)["micro_f1"] - ev.micro(cur_ga)["micro_f1"]
            rec["gate_tp_delta"] = ev.micro(cg)["tp"] - ev.micro(cur_ga)["tp"]
            rec["reason"] = gate.gate_check(cur_ga, cg)
            if rec["reason"]:
                continue
            ct = R.guarded(ev.evaluate_split, cp, split["train"])
            rec["train_f1_delta"] = ev.micro(ct)["micro_f1"] - ev.micro(cur_tr)["micro_f1"]
            lost = gate.losses(cur_tr + cur_ga, ct + cg)
            reruns = {cid: [R.guarded(ev.review_case, cp, by_id[cid], R.rerun_cache, True) for _ in range(2)]
                      for cid in lost}
            rec["confirm_requeried"] = lost
            rec["reason"] = gate.confirm(cur_tr + cur_ga, ct + cg, reruns, split["train"] + split["gate"])
            if not rec["reason"]:
                passers.append((cp, ct, cg, rec, patch))
        passers.sort(key=lambda x: (-ev.micro(x[2])["micro_f1"],
                                    abs(len(x[4]["new_text"]) - len(cur["sections"][x[4]["section"]]))))
        winner = None
        for cp, ct, cg, rec, patch in passers[:MAX_RERUNS]:
            rg = R.guarded(ev.evaluate_split, cp, split["gate"], R.rerun_cache, True)
            rec["rerun_gate_f1"] = ev.micro(rg)["micro_f1"]
            rec["reason"] = gate.stable(cur_ga, rg)
            if rec["reason"] is None:
                winner = (cp, ct, cg, rec, patch)
                break
        for cp, ct, cg, rec, patch in passers:
            if winner and rec is not winner[3] and rec["reason"] is None:
                rec["detail"] = "passed the gate; a higher-ranked candidate won this round"
        if winner:
            cp, ct, cg, rec, patch = winner
            rec["approved"] = True
            save_json(os.path.join(R.dir, "prompts", f"{version}.json"), cp)
            st["accepted"].append({"round": rnd, "version": version, "section": patch["section"],
                                   "old": cur["sections"][patch["section"]], "new": patch["new_text"],
                                   "gate_f1_delta": rec["gate_f1_delta"], "gate_tp_delta": rec["gate_tp_delta"],
                                   "train_f1_delta": rec["train_f1_delta"]})
            cur, cur_tr, cur_ga, streak = cp, ct, cg, 0
        else:
            streak += 1
        for rec in records:
            R.log(rec)
            if rec["reason"]:
                rejections.append({"round": rnd, "section": rec["section"] or "?", "cluster": cluster,
                                   "rationale": rec["rationale"] or "(invalid reply)", "reason": rec["reason"]})
            print(f"round {rnd} cand {rec['cand']}: {'APPROVED ' + version if rec['approved'] else rec['reason'] or 'passed, not selected'}"
                  + (f" (gate dF1 {rec['gate_f1_delta']:+.3f})" if rec["gate_f1_delta"] is not None else "")
                  + (f" [{rec['detail']}]" if rec["detail"] and rec["reason"] == "precheck_failed" else ""))
        st["history"].append({"round": rnd, "version": cur["version"], "gate_f1": ev.micro(cur_ga)["micro_f1"],
                              "train_f1": ev.micro(cur_tr)["micro_f1"]})
        R.save()
        if streak >= STOP_AFTER:
            st["stop_reason"] = "no_progress"
            break
    st["stop_reason"] = st["stop_reason"] or "max_rounds"
    st["final_version"] = cur["version"]
    st["final"] = {"train": summary(cur_tr, split["train"]), "gate": summary(cur_ga, split["gate"])}
    calls = gc.network_calls  # S8: the final prompt re-scored from cache must match exactly with 0 calls
    rep = ev.evaluate_split(cur, split["gate"])
    st["reproducibility"] = {"gate_f1_delta": ev.micro(rep)["micro_f1"] - ev.micro(cur_ga)["micro_f1"],
                             "network_calls": gc.network_calls - calls}
    if not a.skip_test:
        st["test"] = score_test(R, digest, base, cur, split["test"], a.reuse_test)
    st["completed"] = True
    R.save()
    import report
    report.write(R.dir)
    print(f"stop reason: {st['stop_reason']}  final {cur['version']}  run folder {os.path.relpath(R.dir, gd.ROOT)}")
    return 0


def score_test(R, digest, base, final, test_cases, reuse):
    """G7: each prompt is scored on test once per dataset hash, unless --reuse-test (which flags the report)."""
    ledger = json.load(open(LEDGER, encoding="utf-8")) if os.path.exists(LEDGER) else {}
    seen = ledger.setdefault(digest, {})
    out = {}
    for label, prompt in (("v000", base), ("final", final)):
        h = prompt_hash(prompt)
        if label == "final" and h == prompt_hash(base):
            out["final"] = out["v000"]
            continue
        if h in seen and not reuse:
            R.save()
            sys.exit(f"refused: prompt {prompt['version']} ({h[:12]}) was already scored on test for this "
                     "dataset; pass --reuse-test to score it again (the report will say so)")
        res = R.guarded(ev.evaluate_split, prompt, test_cases)
        seen.setdefault(h, []).append({"version": prompt["version"], "run": R.state["run"],
                                       "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
        save_json(LEDGER, ledger)
        out[label] = {"version": prompt["version"], "metrics": summary(res, test_cases),
                      "correct": {r["case_id"]: ev.correct(r) for r in res}}
    return out


def main():
    ap = argparse.ArgumentParser(description="Self-repairing eval harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen-data", help="generate, validate, sample, or freeze the dataset")
    m = g.add_mutually_exclusive_group()
    m.add_argument("--validate", action="store_true", help="generate missing ads, then check C1 to C8")
    m.add_argument("--sample", action="store_true", help="write 15 stratified ads to data/spotcheck.md")
    m.add_argument("--freeze", action="store_true", help="freeze the checked draft and record its hash")
    g.add_argument("--seed", type=int, default=0)
    e = sub.add_parser("eval", help="score one prompt on one split")
    e.add_argument("--split", required=True, choices=["train", "gate", "test"])
    e.add_argument("--prompt", required=True)
    r = sub.add_parser("run", help="the repair loop, then (unless --skip-test) test scoring and the report")
    t = r.add_mutually_exclusive_group()
    t.add_argument("--skip-test", action="store_true", help="pilot: never touch the test split")
    t.add_argument("--reuse-test", action="store_true", help="allow a repeat test scoring; stamps the report")
    a = ap.parse_args()
    return {"gen-data": cmd_gen_data, "eval": cmd_eval, "run": cmd_run}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
