# Build Steps: Self-Repairing Eval Harness (v1)

Companion to PRD.md, EVAL_SPEC.md, and ARCHITECTURE.md. Five steps, in order. Each ends
with acceptance checks you can run. Do not start a step until the previous one passes.
If a check fails, fix the code, never the check. Thresholds are not loosened after you
have seen a result.

Conventions: run from the repo root, with `GROQ_API_KEY` set in your shell. Plain
Python 3, standard library only. Tests use `python -m unittest` and need no API key.

| Step | Delivers | Proves |
|---|---|---|
| 1 | Groq client, cache, limits note | G10, G12, S11 is feasible |
| 2 | Dataset and validator | S9, S10, G9 |
| 3 | Reviewer, eval, baseline | S6, S8 |
| 4 | Repair agent, gate, full healing run | S1 to S5, S7, S12, G1 to G8, G11 |
| 5 | README and a merged pull request | Delivery |

## Step 1: Foundation (Groq client, cache, limits)

Build: `groq_client.py` (retry with backoff, atomic cache, validated reads, key from the
environment), `.gitignore` (`runs/`, caches, env files), `tests/test_client.py`, `LIMITS.md`.

Acceptance checks
1. `python groq_client.py --ping`
   Expected: one reply from each of `openai/gpt-oss-20b` and `openai/gpt-oss-120b`, with
   model id and any rate-limit headers returned. Exit code 0.
2. Run the same command again.
   Expected: both lines say `cache hit` and the network call counter reads 0.
3. `python -m unittest tests.test_client`
   Expected: all pass. Cases: a simulated 429 then success, a truncated cache file
   discarded and re-fetched, a `finish_reason` of length retried once, and the key never
   present in a cache entry.
4. Open `LIMITS.md`, which you fill in from your Groq console: requests and tokens per
   minute and per day, for both models. Compare with ARCHITECTURE.md section 8 (about 90
   calls for data, about 1,750 worst case for 8 rounds).
   Expected: it fits, or you lower `ROUNDS` and `CANDIDATES` in `run.py` and write the new
   worst case in `LIMITS.md`.

## Step 2: Dataset and validator

Build: `gen_data.py` (recipes, generation, validators for C1 to C8, 15-ad sampler,
keyword baseline, freeze and hash), `tests/test_validator.py`.

Acceptance checks
1. `python run.py gen-data --validate`
   Expected: a coverage table, C1 to C8 all PASS, split sizes 36 / 27 / 27, clean counts
   10 / 8 / 8. Exit code 0.
2. `python -m unittest tests.test_validator`
   Expected: all pass. Each constraint has a deliberately broken fixture the validator
   must reject: a rule missing from train, a quote that is not in the ad, a
   near-duplicate pair across splits, a held-out split without N1, one with fewer than 2
   adversarial ads, and a clean/violating length mismatch.
3. `python run.py gen-data --sample`
   Expected: 15 stratified ads with labels (6 one-per-rule, 3 clean, 2 adversarial, N1,
   3 random). You read them and record the result in `data/spotcheck.md`.
   Pass: at least 14 of 15 labels correct (S9). Otherwise fix, regenerate, resample 15.
4. `python run.py gen-data --freeze`
   Expected: `data/dataset.jsonl` plus its sha256, and the keyword baseline micro F1 for
   each split. Pass: below 0.70 on gate and test (S10). Otherwise regenerate harder ads.
5. Change one character in `data/dataset.jsonl`, then `python run.py eval --split train
   --prompt prompts/v000.json`.
   Expected: abort with a hash mismatch before any API call (G9). Restore the file.

## Step 3: Reviewer, eval, and baseline

Build: `evaluate.py` (reviewer call, JSON parse, matching rule, metrics, failure
clusters), `prompts/v000.json` (naive but reasonable, four named sections), `tests/test_eval.py`.
You review `v000.json` before any run.

Acceptance checks
1. `python -m unittest tests.test_eval`
   Expected: all pass. Hand-computed fixtures cover: right rule with a junk quote counts as
   one false positive and one false negative, an off-span quote, a made-up quote, a
   duplicate prediction, a clean ad with a prediction (clean_fpr), and micro F1 to three
   decimals.
2. `python run.py eval --split train --prompt prompts/v000.json`
   Expected: micro F1, precision, recall, case accuracy, per-rule recall, clean_fpr,
   off_span_rate, hallucinated_quote_rate, and parse_failure_rate, each with counts.
   Parse failures at most 5%. Results written to a JSONL file.
3. Run the same command again.
   Expected: 0 network calls and identical numbers (delta 0.000, S8).
4. Headroom check on the printed output.
   Expected: train micro F1 between 0.35 and 0.80, and the largest failure cluster has at
   least 3 cases. Outside that range, the baseline is broken or too good to repair; edit
   `v000.json` (you approve the edit) and repeat checks 2 and 3. These bounds are my picks.
5. `python run.py eval --split gate --prompt prompts/v000.json`, then
   `python run.py eval --split test --prompt prompts/v000.json`.
   Expected: gate scores print. The test command is refused with a message that test is
   scored only inside `run` (G7).

## Step 4: Repair agent, gate, and the full healing run

Build: `repair.py`, `gate.py`, the loop in `run.py`, `report.py`, and `tests/test_gate.py`
and `tests/test_repair.py`.

Acceptance checks
1. `python -m unittest tests.test_gate`
   Expected: all pass, with no API calls. Each of the six reason codes fires on its own
   fixture, a clean improvement is approved, and the three bad candidates ("flag
   everything", "flag nothing", "helps one rule, wrecks another") are rejected 3 of 3 (S7).
   A fixture with noise-only flips shows confirmation clearing them.
2. `python -m unittest tests.test_repair`
   Expected: all pass. Prechecks reject a patch to the locked section, growth over 25%, a
   6-word run copied from any dataset ad, a non-JSON reply, and an extra key. The built
   repair prompt contains no gate or test text or ID (G6), and an injection-style ad in the
   examples appears only as quoted data (G11).
3. `python run.py run --skip-test`
   Expected: a pilot with no test scoring. Exit code 0, a printed stop reason (`max_rounds`
   or `no_progress`), and `runs/<timestamp>/` holding `prompts/`, `candidates.jsonl`, and the
   cache. Use pilots to tune; they never touch the test split.
4. `python run.py run`
   Expected: the full run. It also scores test once for v000 and once for the final
   prompt, and writes `REPORT.md` and `report.svg` into the run folder.
5. `python report.py runs/<timestamp> --criteria`
   Expected: S1 to S12 each printed as PASS or FAIL with the measured value and no missing
   values. This table is the result; the step is complete when it prints.
   The demo is ready when all rows PASS. If some fail, report them as they are. For a
   failed S1, read the reason codes in `candidates.jsonl`: the common code names the cause.
   Levers that do not touch test are the baseline prompt, `ROUNDS`, and `CANDIDATES`. A
   second full run needs `--reuse-test`, which stamps the report.
6. `grep -r "$GROQ_API_KEY" runs data` and `cat data/test_ledger.json`
   Expected: grep prints nothing (G10). The ledger shows exactly two test scorings for this
   dataset hash, v000 and the final prompt (G7).

## Step 5: README and a merged pull request

Build: `README.md` with sections Problem, How it works, Quickstart, Results, Limits; a
`docs/` folder with the four Markdown documents; `docs/results/` with the final
`REPORT.md` and `report.svg`; a branch `selfrepair-v1`; a pull request into `main`.

Acceptance checks
1. `grep -E "^## (Problem|How it works|Quickstart|Results|Limits)" README.md | wc -l`
   Expected: 5.
2. `grep -n "micro F1" README.md docs/results/REPORT.md`
   Expected: the baseline and final test numbers in the README match the report. The
   Limits section states that ads are synthetic and the splits are small.
3. `git clone <repo-url> /tmp/sr-check && cd /tmp/sr-check && git checkout selfrepair-v1
   && python -m unittest discover tests`
   Expected: all tests pass with `GROQ_API_KEY` unset.
4. `git ls-files | grep -E "^runs/|cache/|\.env"` and `git log -p | grep -cE "gs[k]_[A-Za-z0-9]{20,}"`
   Expected: the first prints nothing and the second prints 0. The pattern matches only
   key-shaped strings, and the `[k]` keeps this line from matching itself.
5. `gh pr create --base main --head selfrepair-v1 --fill`, then `gh pr view --json body`
   Expected: the body holds the results table and links to the four documents.
6. Merge it, then `gh pr view --json state,mergedAt`
   Expected: state `MERGED` with a `mergedAt` timestamp.
7. `git checkout main && git pull && ls README.md docs && python -m unittest discover tests`
   Expected: the files are on `main` and all tests pass.

## Open items

1. Repository `<owner>/<repo>` and the base branch name are not chosen yet.
2. Fill `LIMITS.md` and approve `v000.json` before steps 1 and 3 can pass.
