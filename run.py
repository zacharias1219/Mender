"""CLI for the self-repairing eval harness.

  python run.py gen-data [--validate | --sample [--seed N] | --freeze]
  python run.py eval --split {train,gate,test} --prompt prompts/vNNN.json
"""
import argparse
import os
import sys

import gen_data as gd
import groq_client as gc


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
    check_dataset()
    if a.split == "test":
        sys.exit("refused: the test split is scored only inside `python run.py run` (G7)")
    sys.exit("eval: the reviewer is built in Step 3")


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
    a = ap.parse_args()
    return {"gen-data": cmd_gen_data, "eval": cmd_eval}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
