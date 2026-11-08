"""Offline repair tests: prechecks, schema, G6 (no gate/test data), G11 (ads only as quoted data)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import evaluate as ev  # noqa: E402
import gen_data as gd  # noqa: E402
import repair  # noqa: E402
from gate import _result as res  # noqa: E402

BASE = ev.load_prompt(os.path.join(os.path.dirname(__file__), "..", "prompts", "v000.json"))
CASES = gd.read_jsonl(gd.DATASET)
TRAIN = [c for c in CASES if c["split"] == "train"]
ADS = [c["text"] for c in CASES]


def patch(section="decision_rules", text="Flag R1 when a required notice is absent.", **extra):
    return {"section": section, "new_text": text, "rationale": "r", "cluster": "R1 missed", **extra}


class PrecheckTest(unittest.TestCase):
    def test_valid_patch_passes(self):
        self.assertIsNone(repair.precheck(patch(), BASE, BASE, ADS))

    def test_locked_section_rejected(self):
        self.assertIn("locked", repair.precheck(patch("output_format", "Return anything."), BASE, BASE, ADS))

    def test_growth_over_25_percent_rejected(self):
        big = "Check every notice carefully. " * 60
        self.assertIn("grows", repair.precheck(patch(text=big), BASE, BASE, ADS))

    def test_six_word_run_from_any_dataset_ad_rejected(self):
        ad = next(c for c in CASES if c["split"] == "test")["text"]
        run = " ".join(ad.split()[3:9])
        self.assertIn("6-word run", repair.precheck(patch(text=f"Flag ads like: {run}."), BASE, BASE, ADS))

    def test_non_json_reply_rejects_every_candidate(self):
        out = repair.parse_patches("Sure! Here are my ideas.", 2)
        self.assertEqual([p for p, _ in out], [None, None])

    def test_extra_or_missing_key_rejected(self):
        reply = json.dumps({"patches": [patch(extra_key="x"), {"section": "role", "new_text": "y"}, patch()]})
        out = repair.parse_patches(reply, 3)
        self.assertEqual([p is None for p, _ in out], [True, True, False])

    def test_too_few_patches_fill_with_rejections(self):
        out = repair.parse_patches(json.dumps({"patches": [patch()]}), 2)
        self.assertEqual([p is None for p, _ in out], [False, True])


class RepairInputTest(unittest.TestCase):
    def build(self):
        a2 = next(c for c in TRAIN if "a2" in c["flags"])  # injection-style ad, labeled R4 + R5
        r5 = [c["id"] for c in TRAIN if "R5" in {l["rule"] for l in c["labels"]}]
        missed = {a2["id"]} | set(r5[:3])  # R5 missed on the injection ad and 2 others: largest cluster
        results = []
        for c in TRAIN:
            labeled = [l["rule"] for l in c["labels"]]
            results.append(res(c, [r for r in labeled if not (r == "R5" and c["id"] in missed)]))
            results[-1]["preds"] = []
        rejections = [{"round": 1, "section": "role", "rationale": "tried", "reason": "no_f1_gain"}]
        msgs, cluster = repair.build_messages(BASE, BASE, results, TRAIN, rejections, 2)
        return a2, msgs, cluster

    def test_no_gate_or_test_text_or_id_in_repair_prompt(self):
        _, msgs, _ = self.build()
        blob = "\n".join(m["content"] for m in msgs)
        for c in CASES:
            if c["split"] != "train":
                self.assertNotIn(c["id"], blob)
                self.assertNotIn(c["text"][:60], blob)
        self.assertIn("no_f1_gain", blob)  # reason codes are the only gate feedback

    def test_gate_or_test_cases_are_refused(self):
        gate_case = next(c for c in CASES if c["split"] == "gate")
        with self.assertRaises(ValueError):
            repair.build_messages(BASE, BASE, [res(gate_case, [])], [gate_case], [], 2)

    def test_injection_ad_appears_only_as_quoted_data(self):
        a2, msgs, _ = self.build()
        system, user = msgs
        self.assertNotIn(a2["text"], system["content"])
        self.assertIn("<<<AD\n" + a2["text"] + "\nAD>>>", user["content"])
        self.assertIn("never instructions", system["content"])

    def test_budget_keeps_prompt_within_growth_cap(self):
        budget, limit = repair.budgets(BASE, BASE)
        self.assertEqual(limit, int(1.25 * repair.prompt_len(BASE)))
        for s, b in budget.items():
            self.assertEqual(b, len(BASE["sections"][s]) + limit - repair.prompt_len(BASE))


if __name__ == "__main__":
    unittest.main()
