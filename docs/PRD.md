# PRD: Self-Repairing Eval Harness (v1)

Owner: Richard | Status: Final v1, ready to build | Scope: one demo, one reviewer prompt

## 1. Problem

Teams build evals to learn when an AI agent is wrong. Fixing it is still manual:
a person edits the prompt, guesses, and hopes nothing else breaks. Evals detect
failure but do not close the loop, and a fix that helps one failure class often
silently hurts another.

## 2. Uses

- Demo: show that an agent can propose prompt fixes and a strict gate admits only
  fixes that improve results without regressions.
- Reference: a small, readable pattern (eval -> repair -> gate) others can copy.
- Test bed: swap the reviewer task or dataset later without changing the loop.

## 3. What v1 is

A plain-Python harness (standard library only) with four parts:

1. Reviewer: `openai/gpt-oss-20b` reads a synthetic telehealth ad and returns
   strict JSON listing compliance violations (six ad rules, see EVAL_SPEC.md).
2. Eval: scores the reviewer against labeled cases, per case and per rule.
3. Repair agent: `openai/gpt-oss-120b` proposes patches to one section of the
   reviewer prompt, based on train failures only.
4. Gate: deterministic code that approves a patch only if it improves results
   and breaks nothing.

Data: 90 LLM-generated ads (36 train, 27 gate, 27 test), 26 clean. Every rule appears
at least twice in train and at least once in each held-out split, and each held-out
split has 2 clean (one near-miss) and 2 adversarial ads. Spot-checked, frozen, hashed.
Runtime: Groq free tier, with retry, backoff, and a disk cache.

## 4. What v1 is not

- Not a framework, web UI, or multi-agent system.
- Not real data or legal advice. Ads are synthetic, so results describe the loop only.
- Not a general prompt optimizer. It edits one prompt, in allowed sections only.
- Not self-applying outside the reviewer prompt.
- Not scored on severity. Severity is emitted but never counted.
- Not judged by an LLM. Scoring and gating are deterministic code.

## 5. Guardrails

Repair agent
- Sees train failures only (up to 5 from the largest failure cluster), never gate
  or test data, and receives only coarse reason codes from the gate.
- Has no tools or file access. Example ads arrive as quoted data, and its reply must
  pass strict JSON and schema checks.
- Patches one named section per candidate. The output-format section is locked.
- Auto-rejected before the gate for: any 6-word run copied from a dataset ad, a
  disallowed section, or prompt growth over 25% versus baseline.

Gate (approve only if all hold)
- Gate-set micro F1 up by at least +0.03 and at least 2 more true-positive pairs.
- No rule's recall drops.
- No previously fully correct case (train and gate) becomes wrong.
- Drops and regressions count only if 2 cache-bypassed re-queries confirm them.
- Parse-failure rate does not rise.
- A winner is re-run once with the cache bypassed and must pass again.
- One winner per round: highest gate F1, smaller diff on ties.

Run
- Stops at 8 rounds, or 3 consecutive rounds with every candidate rejected.
- Test split is scored once per prompt, after the loop; a ledger refuses repeats.
- `GROQ_API_KEY` comes from the environment only, never written to disk or logs.
- Dataset hash is recorded in every run so results stay comparable.

## 6. Success criteria

Definitions
- Detection: rule matches; the quote is verbatim in the ad and overlaps a labeled span.
- Micro F1: F1 over all violations pooled across the split (headline metric).
- Case accuracy: share of cases where the detected set equals the labeled set exactly
  (a clean case is correct only if nothing is flagged).
- Parse-failure rate: share of cases with invalid JSON or an off-taxonomy type.

All numbers are reported to three decimals and are computed from the frozen dataset.
Targets are pass/fail; results are reported whether or not they are met.

| # | Criterion | Target |
|---|-----------|--------|
| S1 | Accepted patch: at least one patch approved by the gate | >= 1 patch |
| S1a | That patch raises train micro F1 | delta > 0.000 |
| S1b | That patch raises gate micro F1 and true-positive pairs | delta >= +0.030, >= +2 pairs |
| S1c | That patch causes no per-rule recall drop and no case regression (train and gate) | 0 confirmed drops or regressions |
| S2 | Rejected patch: at least one candidate rejected with a logged reason code | >= 1 candidate, code present in log |
| S3 | Final held-out test micro F1 versus baseline | >= baseline + 0.050, more cases improved than worsened |
| S4 | Final held-out test case accuracy versus baseline | >= baseline + 0.050 |
| S5 | Final held-out per-rule recall versus baseline | no rule below baseline |
| S6 | Parse-failure rate, final prompt | <= baseline and <= 5% |
| S7 | Gate self-test: hand-made bad candidates ("flag everything", "flag nothing", "helps one rule, wrecks another") | 3 of 3 rejected |
| S8 | Reproducibility: re-run the eval on the same prompt from cache | score delta = 0.000 |
| S9 | Dataset quality: 15 sampled labels checked by hand before freezing | >= 14 of 15 correct |
| S10 | Dataset shape and shortcut audit | 90 ads, 26 clean, rule floors met, keyword baseline F1 < 0.70 on gate and test |
| S11 | Budget: a full run completes on the free tier | 0 unrecovered API failures |
| S12 | Final test: clean-ad false-positive rate and adversarial recall | FPR <= baseline, recall >= baseline |

Note on S1 and held-out regression: the test split is scored only once, so the
per-patch "no held-out regression" check is measured on the gate split, which the
repair agent never sees. The end-to-end held-out claim is S3, S4, and S5.

Visual evidence (same numbers, no new metrics): `REPORT.md` with baseline versus final
tables, per-rule recall bars, accepted diffs, and rejections by reason code; `report.svg`
with micro F1 by round and per-rule recall before and after, built with the standard library.

## 7. Deliverables

`runs/<timestamp>/` holding prompt versions v000 onward, a JSONL log of every
candidate (patch, rationale, verdict, reason code), the frozen dataset and its hash,
`REPORT.md`, and `report.svg`. Plus a short README for a demo audience.

## 8. Open items before build

1. Confirm Groq limits and model IDs against dataset size and round cap (build step 1).
2. Richard reviews the baseline prompt before any run.
3. Richard spot-checks the sampled dataset before it is frozen.
