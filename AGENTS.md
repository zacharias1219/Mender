# AGENTS.md: Mender (Self-Repairing Eval Harness v1)

Shared instructions for every coding agent in this repo (Claude Code reads them through
`CLAUDE.md`). The specs are the source of truth: `docs/PRD.md`, `docs/EVAL_SPEC.md`,
`docs/ARCHITECTURE.md`, `docs/BUILD_STEPS.md`. If this file and a spec disagree, the spec
wins. Fix this file in the same change.

## Working rules (non-negotiable)

1. **Plain Python 3, standard library only.** No pip installs, no `requirements.txt`, and no
   `requests`, `openai`, `groq`, `pydantic`, or `pytest`. HTTP uses `urllib.request`, tests
   use `unittest`.
2. **No agentic framework.** No LangChain, LlamaIndex, DSPy, CrewAI, AutoGen, or agent
   SDKs. The repair agent is one plain chat-completion call with no tools (G11).
3. **Make the smallest change that works.** Build only what the current build step needs.
   No speculative abstractions, config systems, plugins, or extra files. Prefer one
   function to a class, and one file to a package.
4. **Never print, log, cache, or commit a secret.** `GROQ_API_KEY` is read from the
   environment at call time and only placed in the `Authorization` header. Never echo
   it, never put it in an error message, and never write it to `runs/`, cache entries,
   or logs. Before every commit, run `git diff --cached | grep -E 'gs[k]_[A-Za-z0-9]{20,}'`. It must
   print nothing (G10). Never type the key prefix literally in a tracked file.
5. **Verify with a real run before saying anything is done.** "Done" means the step's
   acceptance commands in `docs/BUILD_STEPS.md` were run here and their output was seen.
   Unit tests alone don't count for checks that call the API. Report failures as they
   happened, with the output.
6. **Fix the code, never the check.** Never loosen a threshold after seeing a result.
   Never skip or disable a test to get green. Steps run in order 1 to 5, and a step
   starts only once the previous one passes.
7. **Never touch the test split outside the final step of `run.py run`.** No ad-hoc
   test scoring, no peeking at test text while tuning (G7).
8. **The human approves what is theirs:** `prompts/v000.json`, `LIMITS.md` values, the
   `data/spotcheck.md` verdicts, and any edit to the baseline prompt. Agents prepare these
   files, and Richard signs off on them.

## Project in brief

- Goal: a demo where an LLM proposes reviewer-prompt patches and a deterministic gate
  admits only patches that improve held-out results with no confirmed regression.
- Reviewer: `openai/gpt-oss-20b`, which reads a synthetic telehealth ad and returns
  `{"violations":[{"rule","evidence_quote","severity"}]}`. Severity is never scored.
- Repair agent and data generator: `openai/gpt-oss-120b`.
- API: Groq, OpenAI-compatible, `POST https://api.groq.com/openai/v1/chat/completions`.
- Rules R1 to R6 (closed list): missing_disclosure, regulatory_claim, absolute_safety,
  testimonial, false_urgency, cure_claim. Full definitions are in `docs/EVAL_SPEC.md` §2.
- Dataset: 90 ads (train 36, gate 27, test 27), clean ads 10/8/8. The constraints C1 to C8
  are in EVAL_SPEC §3. The frozen `data/dataset.jsonl` is committed and its sha256 is
  checked at every run start (G9).

## Key constants (from the specs, don't change silently)

| Constant | Value |
|---|---|
| Gate F1 gain / TP-pair gain | >= +0.03 / >= +2 |
| Confirmation | 2 cache-bypassed re-queries, loss counts if it fails 2 of 3 |
| Winner stability rerun | gate split, cache bypassed, up to 3 reruns per round |
| ROUNDS / CANDIDATES | 8 / 3 (lower only via LIMITS.md, before any full run) |
| Stop | 8 rounds, or 3 consecutive all-rejected rounds. Abort on hash mismatch or 3 consecutive exhausted API calls |
| Repair prechecks | locked `output_format` byte-identical, growth <= 25% vs baseline, no 6-word run copied from any dataset ad |
| Repair context | train failures only: largest cluster, up to 5 examples, plus reason codes |
| Reason codes | `precheck_failed`, `no_f1_gain`, `parse_failures`, `rule_recall_drop`, `case_regression`, `unstable_on_rerun` |
| Shortcut audit | keyword-baseline micro F1 < 0.70 on gate and on test |
| Spot-check | >= 14 of 15 correct |
| Baseline headroom | train micro F1 in [0.35, 0.80], largest failure cluster >= 3 cases |
| Parse failures | <= 5% |
| Metrics | 3 decimals, with counts beside them |

Quote matching: the quote is a verbatim substring of the ad AND overlaps a labeled span
(containment, or at least half of the shorter span's tokens shared). A correct rule with
a bad quote counts as 1 FP plus 1 FN.

## Layout (repo root = `selfrepair/` in ARCHITECTURE §6)

`groq_client.py gen_data.py evaluate.py repair.py gate.py run.py report.py` sit at the
root. Then: `prompts/v000.json` (sections `role`, `rule_definitions`, `decision_rules`,
and `output_format`, which is locked), `data/` (dataset, `test_ledger.json`,
`spotcheck.md`), `tests/` (offline, no key), `LIMITS.md`, `docs/`, `runs/<timestamp>/`
(gitignored).

## Environment facts (learned in this cloud container)

- Python 3.13. Outbound HTTPS goes through an agent proxy. `SSL_CERT_FILE` is already set,
  so `urllib` works as is. Don't disable TLS verification.
- **Cloudflare blocks Python's default `User-Agent`** with `403 error code: 1010`. Every
  request must send a custom one, such as `User-Agent: mender-selfrepair/1`. With it, Groq
  answers normally (401 without a key).
- Key handling (in place since 2026-11-08): cloud sessions run on the personal "Mender"
  environment. Its network secret for `api.groq.com` is added to requests by the proxy, so
  the real key never enters the container. Run live commands with
  `GROQ_API_KEY=proxy-injected`. The value only gets the client past its check, and the
  proxy replaces the header. For G10, scan for key-shaped strings:
  `grep -rlE 'gs[k]_[A-Za-z0-9]{20,}|Bearer' runs data cache` must print nothing. On a
  local machine with a real key in the shell, also run the literal
  `grep -r "$GROQ_API_KEY"` check.
- The `gh` CLI can't create or merge PRs here. Use the GitHub MCP tools for Step 5.
- The container is ephemeral. Anything worth keeping must be committed and pushed.
  `runs/` isn't committed, so copy the final `REPORT.md` and `report.svg` into
  `docs/results/` (Step 5).

## Decisions and interpretations (keep in sync with the code)

- **Budget:** 200K tokens per day per model binds. A gen call is about 1,500-2,200 tokens
  with `reasoning_effort` "medium" (about 65% of it reasoning). "Low" halves the tokens but
  broke recipes, so generation uses "medium". The loop's ROUNDS and CANDIDATES get fixed in
  LIMITS.md after Step 3 measures reviewer tokens, before any run.
- **C6:** two ads are near-duplicates if they share any 8-gram of lowercased word tokens,
  or have token-set Jaccard >= 0.6 (strict reading). Richard confirmed the strict reading on 2026-11-08.
  Retries (never attempt 0) name this ad's colliding phrases plus the run's most recent ones,
  up to 12. Nearly every collision is notice boilerplate.
- **Typography:** generated ads and quotes are converted to ASCII (non-breaking hyphens,
  curly quotes, nbsp) before validation. This keeps "verbatim substring" matching robust.
- **Recipes** (`gen_data.recipes()`, seed 7) fix the rules and flags per ad. Each prompt
  states which rules to violate, which must not be violated, and which notices to include
  or omit. Retries put the attempt number into the prompt, so every attempt has its own
  cache key and reruns replay exactly.
- **N1** = the single held-out `near_miss` ad (flags stay within the schema: clean,
  near_miss, a1, a2, multi). Train has 3 near-misses, plus one a1 and one a2 with other
  surface forms.
- **Freeze** renames `data/draft.jsonl` to `data/dataset.jsonl`, so the spot-checked bytes
  are the frozen bytes. It refuses unless C1 to C8 pass, `data/spotcheck.md` names this
  draft's hash with `Result: N / 15` where N >= 14, and the keyword baseline is < 0.70 on
  gate and test. The draft is committed while under review.
- **R1 notice (b) trigger:** any mention of a prescription or prescribing counts, even with
  no named drug ("get a prescription"). This is my reading of "prescription medication
  mentioned", for Richard to confirm, and Step 3's rule definitions must say the same.
  `check_case` rejects an ad that isn't labeled R1, mentions prescribing, and lacks
  "clinically" or "appropriate".
- **C8 balance:** compliant ads carry every notice, so they run long. Compliant-ad prompts
  ask for 40-55 words.
- **Generation history (2026-11-08):** it took 3 Groq accounts, because each account's
  200K tokens/day ran out on 120b. Fixes found along the way: Groq returns 400
  `json_validate_failed` for invalid JSON (counted as a failed attempt); curly quotes must
  be normalized after parsing, not before; and rerunning keeps accepted draft ads, so a
  prompt tweak doesn't ripple.
- **Scoring core** (`evaluate.overlaps`, `score_case`, `micro`) was written in Step 2 for
  the keyword baseline. Step 3 adds the reviewer on top of it.

## Git

- Repo `zacharias1219/Mender`. Cloud sessions develop on their assigned `claude/...`
  branch and push only there. Open a PR only when Richard asks.
- BUILD_STEPS Step 5 names branch `selfrepair-v1` into `main`. That is open item 1 below.
- Commit messages are short and imperative. Never commit `runs/`, caches, env files, or keys.

## Status log (update at the end of every work session)

| Date | Step | State | Notes |
|---|---|---|---|
| 2026-11-08 | 0 | done | AGENTS.md, CLAUDE.md, .gitignore, 4 specs in docs/. Step 5 check 4 now matches key-shaped strings only (Richard approved). Groq reachable, UA quirk found |
| 2026-11-08 | 1 | done | groq_client.py and 11 offline tests. Live ping OK on both models, replay 0 calls. LIMITS.md filled (30 RPM, 1K RPD, 8K TPM, 200K TPD per model). Loop budget decision deferred to after Step 3 |
| 2026-11-08 | 2 | done | Frozen data/dataset.jsonl sha256 c6cc2e21d334541ba16715ed231ff5afe62f42b1e4d5de05e16272c50ea073de. C1-C8 PASS, keyword F1 gate 0.548, test 0.407 (S10). Spot-check 15/15: Richard said "all correct" in chat, Claude recorded it (S9). Tamper test aborts before any call (G9) |
| 2026-11-08 | 3 | done | Checks 1-5 pass. v000 rule_definitions made terse with Richard's approval (the first v000 scored train F1 0.896, above the 0.80 headroom bound). v000: train F1 0.769, gate 0.667, R1 recall 0.167/0.000, about 570 tokens per call. Budget ROUNDS=3, CANDIDATES=2 in LIMITS.md |
| 2026-11-08 | 4 | run done; S1 FAIL | 4 pilots (3x2) and 1 full run (3x3, runs/20261108T152027Z), 33 patches, 0 approved. Full run: S2 S5-S12 PASS; S1 S1a-c S3 S4 FAIL. Test (scored once, v000 = final) micro F1 0.809, case acc 0.704. Every reason code but unstable_on_rerun fired on real patches. Evidence in docs/results/. A second full run needs --reuse-test |

## Open items

0. S1 not met. Levers that don't touch test: ROUNDS, CANDIDATES, the baseline prompt, and the repair
   agent's input. Blockers seen: the G3 no-regression rule (the best patch had gate F1 0.667->0.808,
   +5 TP, but broke one correct case), the +2 TP floor (patches that only cut false alarms), and
   the 25% growth cap on a terse 856-char v000. A new full run needs `--reuse-test` (stamped).

1. Final branch and PR target: session branch vs `selfrepair-v1` into `main` (ask Richard).
2. EVAL_SPEC §7.9: revisit using a non-gpt-oss generator after the first full run.
