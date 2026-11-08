# Groq free-tier limits (BUILD_STEPS Step 1, check 4)

Source: Richard's Groq console (Settings, Limits), 2026-11-08. The RPD and TPM rows match the
`x-ratelimit-*` headers returned by `python groq_client.py --ping`.

| Limit | openai/gpt-oss-20b (reviewer) | openai/gpt-oss-120b (generator, repair) |
|---|---|---|
| Requests / minute | 30 | 30 |
| Requests / day | 1,000 | 1,000 |
| Tokens / minute | 8,000 | 8,000 |
| **Tokens / day** | **200,000** | **200,000** |

Reasoning tokens count toward usage. A tiny JSON call on 120b used 245 tokens, of which
109 were reasoning.

## Fit against ARCHITECTURE.md section 8

Tokens per day bind first. The tokens per call below are estimates, until Step 3 measures
`usage`, which is stored in every cache entry.

| Phase | Calls | Est. tokens per call | Est. tokens | Days at 200K |
|---|---|---|---|---|
| Data generation (120b) | ~90 + retries | ~1,500 | ~135K-170K | 1 (fits) |
| Baseline train + gate (20b) | 63 | ~1,500 | ~95K | 0.5 |
| Typical round, 3 candidates (20b) | ~81 gate + ~36 train + ~27 rerun | ~1,500 | ~215K | ~1 |
| Worst case, 8 rounds x 3 (20b) | ~1,750 | ~1,500 | ~2.6M | **~13 (does not fit)** |
| Final test scoring, v000 + final (20b) | 54 | ~1,500 | ~80K | 0.4 |

- **Requests aren't the problem.** 30 per minute and 1,000 per day fit easily. At about
  1,500 tokens per call, 8,000 TPM allows about 5 calls per minute, so a 27-ad split takes
  about 6 minutes. The client honors `Retry-After` on 429.
- **The full run spans several days by design.** When the daily quota runs out, calls
  exhaust their retries and the run aborts. Everything is cached, so rerunning the same
  command the next day resumes at no cost (abort rule 4).

## Decision (pending: must close before any `run.py run`, after Step 3 measures tokens per call)

Levers, in the order to try them. None of them touch the test split.
1. Fewer reviewer tokens per call: `reasoning_effort` "low" and a compact `v000.json`.
2. `CANDIDATES` 3 to 2 (cuts each round by about a third).
3. `ROUNDS` 8 to 4.

The worst case and the chosen values will be written here before any run. They must not
be changed after seeing results.
