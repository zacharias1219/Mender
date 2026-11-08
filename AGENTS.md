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
- Preferred key handling: save the key as a network secret for `api.groq.com` through the
  secure credential form, so the proxy injects it and the real key never enters the
  container. Then set `GROQ_API_KEY` to any placeholder (for example `placeholder-not-a-key`) so
  the client starts. The G10 grep is still run.
- The `gh` CLI can't create or merge PRs here. Use the GitHub MCP tools for Step 5.
- The container is ephemeral. Anything worth keeping must be committed and pushed.
  `runs/` isn't committed, so copy the final `REPORT.md` and `report.svg` into
  `docs/results/` (Step 5).

## Git

- Repo `zacharias1219/Mender`. Cloud sessions develop on their assigned `claude/...`
  branch and push only there. Open a PR only when Richard asks.
- BUILD_STEPS Step 5 names branch `selfrepair-v1` into `main`. That is open item 1 below.
- Commit messages are short and imperative. Never commit `runs/`, caches, env files, or keys.

## Status log (update at the end of every work session)

| Date | Step | State | Notes |
|---|---|---|---|
| 2026-11-08 | 0 | done | AGENTS.md, CLAUDE.md, .gitignore, 3 specs in docs/ (BUILD_STEPS pending, item 5). Groq reachable, UA quirk found |
| | 1 | next | groq_client.py, tests/test_client.py, LIMITS.md (needs key + console limits) |

## Open items

1. Final branch and PR target: session branch vs `selfrepair-v1` into `main` (ask Richard).
2. Richard fills in `LIMITS.md` from the Groq console (Step 1, check 4).
3. Richard approves `prompts/v000.json` before any eval (Step 3).
4. Richard records the 15-ad spot-check in `data/spotcheck.md` before freezing (Step 2).
5. BUILD_STEPS Step 5 check 4 (`git log -p | grep -c` on the key prefix) can never print 0,
   because BUILD_STEPS.md contains that string. Proposed fix: match key-shaped strings only
   (`gs[k]_[A-Za-z0-9]{20,}`). Waiting on Richard. Until then BUILD_STEPS.md isn't committed.
6. EVAL_SPEC §7.9: revisit using a non-gpt-oss generator after the first full run.
