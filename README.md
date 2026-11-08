# Mender: a self-repairing eval harness

An LLM reviewer checks synthetic telehealth ads for six compliance rules. A repair agent
proposes fixes to the reviewer's prompt, and a deterministic gate admits a fix only if it
measurably improves results on ads the agent never sees, without breaking anything else.
Plain Python 3, standard library only, Groq free tier.

## Problem

Evals tell you when an AI agent is wrong, but fixing it is still manual. Someone edits the
prompt, guesses, and hopes nothing else breaks. A fix that helps one failure class often
silently hurts another. Mender closes the loop (eval, then repair, then gate) and treats
every prompt patch as a hypothesis that must earn its way in.

## How it works

```
data/dataset.jsonl (90 ads, frozen, sha256-checked) -> train 36 | gate 27 | test 27
prompts/vNNN -> reviewer (gpt-oss-20b) -> eval (deterministic matching) -> per-case, per-rule results
     ^                                                      | train failures only
     |                                                      v
     +---- approve (best of N, after a rerun) <--- GATE <--- repair agent (gpt-oss-120b): N patches
                                     reject: reason code only
```

- **Reviewer** (`evaluate.py`): returns JSON violations, each with a verbatim evidence
  quote. A detection counts only if the rule matches and the quote is in the ad and
  overlaps a labeled span.
- **Repair agent** (`repair.py`): one plain chat call, with no tools. It sees up to 5 train
  failures from the largest cluster and past reason codes, and never any gate or test ads.
  Prechecks reject locked-section edits, more than 25% growth, and any 6-word run copied
  from a dataset ad.
- **Gate** (`gate.py`, pure code): G1 gate micro F1 +0.03 and +2 TP pairs; G4 parse
  failures do not rise; G2 no rule recall drop and G3 no previously correct case broken,
  pooled over train and gate, with every loss confirmed by 2 cache-bypassed re-queries;
  G5 the winner passes again on a cache-bypassed rerun.
- **Test split**: scored once per prompt, after the loop. `data/test_ledger.json` refuses
  repeats unless `--reuse-test` is passed, which stamps the report.

Specs: [PRD](docs/PRD.md), [Eval spec](docs/EVAL_SPEC.md), [Architecture](docs/ARCHITECTURE.md),
[Build steps](docs/BUILD_STEPS.md). Free-tier budget: [LIMITS.md](LIMITS.md).

## Quickstart

Tests run offline, with no key:

```bash
python -m unittest discover tests
```

Live commands need `GROQ_API_KEY` in your shell. In a Claude Code cloud session the key is
a proxy-injected network secret, so set the variable to any placeholder.

```bash
python groq_client.py --ping
```

```bash
python run.py eval --split train --prompt prompts/v000.json
```

```bash
python run.py run --skip-test
```

```bash
python run.py run
```

```bash
python report.py runs/<timestamp> --criteria
```

`python run.py gen-data --validate | --sample | --freeze` rebuilds the dataset. The frozen
one is committed, and every run checks its hash before any API call.

## Results

Full run `20261108T152027Z`: 3 rounds x 3 candidates, dataset sha256 `c6cc2e21…`. The
complete report is in [docs/results/REPORT.md](docs/results/REPORT.md), with the
[chart](docs/results/report.svg) and the [criteria](docs/results/criteria.txt).

**No patch was approved, so the headline claim (S1, an accepted patch) is not met.** The
final prompt is the baseline v000.

| | Baseline v000 | Final (= v000) |
|---|---|---|
| Test micro F1 | 0.809 (tp 19, fp 3, fn 6) | 0.809 |
| Test case accuracy | 0.704 (19/27) | 0.704 |
| Gate micro F1 | 0.667 | 0.667 |
| Train micro F1 | 0.769 | 0.769 |

| Criterion | Result |
|---|---|
| S1, S1a-c accepted patch | FAIL: 0 of 9 approved (0 of 33 across 4 pilots and the full run) |
| S2 rejection with a reason code | PASS: `no_f1_gain` 4, `precheck_failed` 2, `case_regression` 1, `parse_failures` 1, `rule_recall_drop` 1 |
| S3, S4 test F1 / case accuracy +0.050 | FAIL: no change, since the final prompt is v000 |
| S5, S6, S12 no rule, parse, FPR, or adversarial regression | PASS: nothing changed |
| S7 gate self-test, 3 bad candidates | PASS: 3/3 rejected |
| S8 reproducible from cache | PASS: delta 0.000, 0 network calls |
| S9 spot-check | PASS: 15/15 |
| S10 dataset shape and keyword audit | PASS: 90 ads, 26 clean, keyword F1 0.548 on gate, 0.407 on test |
| S11 free-tier run, no unrecovered API failures | PASS |

What the run shows: the gate does its job. It turned away real patches for every reason it
has except the stability rerun, which is never reached without a passing candidate. The
best pilot patch lifted gate micro F1 from 0.667 to 0.808 (+5 TP pairs) but broke one case
the baseline got right, and the gate refused it. What it does not show yet is a patch good
enough to pass every guarantee.

## Limits

- **Synthetic, small.** All 90 ads, brands, and rules are synthetic, written by
  `gpt-oss-120b`. The splits are small (36/27/27), so one case moves a metric by about 0.03.
  Results describe the loop, not real-world compliance, and none of this is legal advice.
- **S1 not met.** 33 patches, 0 approved. Observed blockers: the strict no-regression rule
  (G3), the +2 TP floor (many patches only removed false alarms), and the 25% growth cap on
  a deliberately terse 856-character baseline.
- **Baseline edited for headroom.** The first v000 scored train F1 0.896, above the 0.80
  headroom bound. With Richard's approval, its rule definitions were cut to one line each
  before any run.
- **Budget reduced.** The free tier allows 200K tokens per day per model. The full run used
  3 rounds x 3 candidates, not the spec's 8 x 3. That was decided before the run and is
  recorded in LIMITS.md. It took four Groq accounts.
- **One model family.** The generator, repair agent, and reviewer are all gpt-oss, so they
  may share blind spots (EVAL_SPEC 7.9).
- **The test split was scored once** (v000 = final). Any further full run must pass
  `--reuse-test`, and its report says so.
- **Spot-check by chat.** The 15/15 result was given in chat and recorded in
  `data/spotcheck.md` by Claude.
