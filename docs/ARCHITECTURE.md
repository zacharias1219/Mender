# Architecture: Self-Repairing Eval Harness (v1)

Companion to PRD.md, EVAL_SPEC.md, and BUILD_STEPS.md. Plain Python, standard library only.

## 1. Shape

Four moving parts around one frozen dataset: a reviewer, an eval, a repair agent, and
a gate. The loop proposes a prompt patch, measures it, and admits it only if the gate
says it improves things without breaking anything.

```
              data/dataset.jsonl (frozen, hashed)
                 | train        | gate         | test (final step only)
                 v              v              v
 prompts/vNNN --> [Reviewer 20b] --> [Eval] --> per-case, per-rule results
        ^                                          |
        |                                          | train failures only
        |                                          v
        |                                   [Repair agent 120b]
        |                                          | 3 candidate patches
        |                                          v
        |    approve: best of 3,            [GATE: pure code]
        |    after stability rerun  <------- reject: reason code only
        v                                          |
 prompts/vNNN+1.json  <----------------------------+
```

## 2. Components

| File | Role | Calls Groq | Kind |
|---|---|---|---|
| groq_client.py | POST chat completions, retry with backoff on 429 and 5xx, disk cache keyed on sha256(model, params, messages) with atomic writes and validated reads, model id and date stored per entry, key from env, `--ping` self-check | yes | Plain |
| gen_data.py | Recipes to 120b, validators, spot-check sampler, freeze and hash | yes | Plain |
| evaluate.py | Run reviewer on a split, parse JSON, apply matching rule, return results and metrics | yes | Plain |
| repair.py | Build context from train failures, ask 120b for 3 patches, run prechecks | yes | Plain |
| gate.py | Pure function: previous results, candidate results, rerun results to verdict and reason code | no | **New** |
| run.py | argparse CLI (`gen-data` with `--validate/--sample/--freeze`, `eval`, `run` with `--skip-test/--reuse-test`), the loop, run folder state | no | Plain |
| report.py | `REPORT.md` and `report.svg` from logged numbers; `--criteria` prints PRD S1 to S12 as PASS or FAIL | no | Plain |

The reviewer prompt lives in `prompts/vNNN.json` as named sections: `role`,
`rule_definitions`, `decision_rules`, `output_format` (locked).

## 3. Ordinary parts and the one new idea

**Ordinary, on purpose:** an HTTP client with a cache, synthetic data generation, a
JSON-output reviewer, a set-matching scorer, an LLM that writes patches, a loop with a
round cap, and a markdown report. Each is a pattern most readers have seen.

**The new idea is the gate contract.** A patch is treated as a hypothesis. It is admitted
only when deterministic code confirms measurable gain on data the proposer cannot see,
and no confirmed regression on any rule or previously correct case. It has three pieces,
all in `gate.py`:

1. A case-level regression ledger: current prompt versus candidate, per case and per rule.
2. A blind feedback channel: the proposer learns only a coarse reason code. At most 24
   verdicts (8 rounds x 3 candidates) ever leave the gate, which bounds what it can learn.
3. Stability and confirmation reruns: a winner must pass again with the cache bypassed,
   and any lost pair or broken case is re-queried twice. Model noise then neither admits
   a patch nor blocks a good one. A single noisy sample over about 60 cases would
   otherwise show some spurious regression most of the time.

If this module is removed, the rest is an ordinary prompt-tuning loop.

## 4. Data flow per round

1. Load the current prompt and its cached baseline results (train, gate).
2. Cluster train failures by rule and error kind (missed or false alarm). Take the largest
   cluster and up to 5 examples.
3. Repair agent receives prompt, examples, train metrics, and past rejections with codes.
   Example ads are passed as quoted data. The agent has no tools or file access.
4. It returns 3 structured patches: section, new text, rationale, target cluster. Any
   reply that fails the schema is a rejected candidate.
5. Prechecks run (locked section, size cap, no 6-word run from a dataset ad).
6. For each survivor, evaluate on the gate split. Check F1 gain, pair gain, and parse
   failures.
7. If those pass, evaluate on train. Check rule recall drops and case regressions,
   confirming any loss with two cache-bypassed re-queries.
8. Rank the passers (highest gate F1, smaller diff on ties). Rerun the best on the gate
   split with the cache bypassed. If it fails, try the next, up to 3 reruns.
9. Approve and write `vNNN+1`, or log the reason codes. Check the stop conditions.

Checks run cheapest first, and the first failure ends that candidate's evaluation. This
keeps free-tier calls down.

## 5. Records (JSONL)

```
case    {id, split, text, labels:[{rule, quotes}], flags:[clean|near_miss|a1|a2|multi]}
result  {case_id, parsed, preds:[{rule, quote}], tp, fp, fn}
patch   {round, cand, section, new_text, rationale, cluster}
verdict {round, cand, approved, reason, gate_f1_delta, train_f1_delta}
```

`train_f1_delta` is null when a candidate is rejected before the train evaluation.

## 6. Layout

```
selfrepair/
  groq_client.py  gen_data.py  evaluate.py  repair.py  gate.py  run.py  report.py
  data/dataset.jsonl            frozen, sha256 recorded in every run
  data/test_ledger.json         which prompts have been scored on test, per dataset hash
  data/spotcheck.md             your 15-ad label check, recorded by hand
  tests/                        offline unittest suite, no API key needed
  LIMITS.md                     free-tier limits, recorded by hand
  docs/                         the four Markdown documents and final results
  prompts/v000.json             baseline written by hand, reviewed before any run
  .gitignore                    runs/, cache, and any env file
  runs/<timestamp>/
    prompts/vNNN.json           accepted versions
    candidates.jsonl            every patch, rationale, verdict, reason code
    cache/                      Groq responses
    REPORT.md  report.svg
  README.md
```

## 7. Failure behavior

| Event | Behavior |
|---|---|
| 429 or 5xx | Backoff and retry. 3 consecutive exhausted calls abort the run, resumable from cache |
| Reviewer returns bad JSON | Case counts as failed, feeds parse_failure_rate |
| Repair returns bad JSON | Candidate rejected as `precheck_failed`, round continues |
| Reply cut off (`finish_reason` is length) | Retry once with double max_tokens, then count as a parse failure |
| Cache entry corrupt or partial | Discarded and re-fetched |
| Dataset hash mismatch | Abort before any call |
| Second test scoring of the same prompt | Refused by the ledger unless `--reuse-test`, which flags the report |

## 8. Call budget (to verify against Groq free-tier limits)

- Data generation: about 90 calls to 120b, plus retries.
- Per candidate, worst case: 27 gate + 36 train = 63 reviewer calls. Baseline is cached.
- Per round, worst case: 3 x 63, one 27-call rerun, 3 repair calls, about 220 calls.
- Confirmation re-queries add 2 calls per lost case, usually under 20 per candidate.
- A full 8-round worst case is about 1,750 calls. Short-circuiting and the cache bring
  the typical run well below that. Rounds or candidates are the levers if limits bind.
