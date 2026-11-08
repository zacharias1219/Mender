"""Offline gate tests: every reason code on its own fixture, approval, self-test, noise. No API calls."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gate  # noqa: E402
import gen_data as gd  # noqa: E402
import repair  # noqa: E402
from gate import _result as res  # noqa: E402


def case(cid, split, rules):
    return {"id": cid, "split": split, "flags": [] if rules else ["clean"], "text": f"ad {cid}",
            "labels": [{"rule": r, "quotes": ["x"]} for r in rules]}


GATE = [case(f"g{i}", "gate", ["R2"]) for i in range(10)] + [case("gc", "gate", [])]
TRAIN = [case("t1", "train", ["R3"]), case("t2", "train", ["R3"]), case("tc", "train", [])]
CASES = GATE + TRAIN


def results(found_gate, train=None, parsed=None, fp=None):
    """found_gate: how many of g0..g9 are found. train: {id: tp_rules}. fp: {id: fp_rules}."""
    train, parsed, fp = train or {}, parsed or {}, fp or {}
    g = [res(c, ["R2"] if i < found_gate else [], fp.get(c["id"], ()), parsed.get(c["id"], True))
         for i, c in enumerate(GATE[:10])] + [res(GATE[10], [], fp.get("gc", ()), parsed.get("gc", True))]
    t = [res(c, train.get(c["id"], [l["rule"] for l in c["labels"]]), fp.get(c["id"], ())) for c in TRAIN]
    return g, t


PREV_G, PREV_T = results(6)  # gate F1 0.750 (tp 6, fn 4); train fully correct


def verdict(cand_g, cand_t, rerun=None, stability=None):
    """The full gate in run.py's order. rerun(case_id) -> list of 2 rerun results."""
    reason = gate.gate_check(PREV_G, cand_g)
    if reason:
        return reason
    lost = gate.losses(PREV_T + PREV_G, cand_t + cand_g)
    by_id = {r["case_id"]: r for r in cand_t + cand_g}
    reruns = {cid: (rerun(cid) if rerun else [by_id[cid], by_id[cid]]) for cid in lost}
    reason = gate.confirm(PREV_T + PREV_G, cand_t + cand_g, reruns, CASES)
    if reason:
        return reason
    return gate.stable(PREV_G, stability if stability is not None else cand_g)


class ReasonCodeTest(unittest.TestCase):
    def test_clean_improvement_is_approved(self):
        g, t = results(9)  # +3 TP pairs, F1 0.750 -> 0.947
        self.assertIsNone(verdict(g, t))

    def test_precheck_failed(self):
        base = {"version": "v000", "sections": {s: "x" * 50 for s in gate.ev.SECTIONS}}
        patch = {"section": "output_format", "new_text": "changed", "rationale": "", "cluster": ""}
        self.assertIsNotNone(repair.precheck(patch, base, base, ["some ad"]))  # run.py logs precheck_failed

    def test_no_f1_gain(self):
        self.assertEqual(verdict(*results(6)), "no_f1_gain")
        self.assertEqual(verdict(*results(7)), "no_f1_gain")  # +1 pair is below the +2 pair floor

    def test_parse_failures(self):
        g, t = results(9, parsed={"gc": False})
        self.assertEqual(verdict(g, t), "parse_failures")

    def test_rule_recall_drop(self):
        g, t = results(9, train={"t1": []})  # R3 recall on train 2/2 -> 1/2, confirmed by both reruns
        self.assertEqual(verdict(g, t), "rule_recall_drop")

    def test_case_regression(self):
        g, t = results(9, fp={"tc": ["R5"]})  # a correct clean ad gets a false alarm; no recall drops
        self.assertEqual(verdict(g, t), "case_regression")

    def test_unstable_on_rerun(self):
        g, t = results(9)
        self.assertEqual(verdict(g, t, stability=results(6)[0]), "unstable_on_rerun")


class ConfirmationTest(unittest.TestCase):
    def test_noise_only_flip_is_cleared_by_confirmation(self):
        g, t = results(9, train={"t1": []})  # t1 looks lost in the first sample...
        good = res(TRAIN[0], ["R3"])
        self.assertIsNone(verdict(g, t, rerun=lambda cid: [good, good]))  # ...but both reruns find it

    def test_loss_in_two_of_three_runs_counts(self):
        g, t = results(9, train={"t1": []})
        good, bad = res(TRAIN[0], ["R3"]), res(TRAIN[0], [])
        self.assertEqual(verdict(g, t, rerun=lambda cid: [good, bad]), "rule_recall_drop")

    def test_losses_finds_lost_pairs_and_broken_cases_only(self):
        g, t = results(9, train={"t1": []}, fp={"tc": ["R5"]})
        self.assertEqual(sorted(gate.losses(PREV_T + PREV_G, t + g)), ["t1", "tc"])


class SelfTest(unittest.TestCase):
    def test_three_bad_candidates_rejected_on_real_dataset(self):
        cases = [c for c in gd.read_jsonl(gd.DATASET) if c["split"] != "test"]
        out = gate.self_test(cases)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(out.values()), out)
        self.assertEqual(out["helps one rule, wrecks another"], "rule_recall_drop")


if __name__ == "__main__":
    unittest.main()
