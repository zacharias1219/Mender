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

## Decision (recorded 2026-11-08, before any `run.py run`)

Richard chose **ROUNDS = 3, CANDIDATES = 2** for a single proof run. Thresholds and
guarantees are unchanged. Measured reviewer cost (Step 3): about 570 tokens per call
(v000: 20,367 tokens over 36 train calls, 15,445 over 27 gate calls).

| Item | Calls (worst case) | Tokens at ~570/call |
|---|---|---|
| Baseline train + gate (cached after Step 3) | 63 | 0 new |
| Per candidate: gate 27 + train 36 + confirmations up to ~20 | ~83 | ~47K |
| Per round: 2 candidates + up to 2 stability reruns of 27 + 1 repair call (120b) | ~220 | ~125K |
| 3 rounds | ~660 | ~375K |
| Final test scoring, v000 + final | 54 | ~31K |
| **Total worst case (20b)** | **~715** | **~405K, about 2 accounts' daily 20b quota** |

Short-circuiting (most candidates stop after the 27-ad gate check) makes a typical run
about half of that. If a quota runs out, the run aborts and is resumable from cache, after
the key is swapped through the secure form.

## Update (2026-11-08, after 4 pilots, before the full run)

Richard raised **CANDIDATES from 2 to 3** (the spec default), with ROUNDS staying at 3. The
four pilots (3 x 2 each) proposed 24 patches and the gate approved none. The best patch
raised gate F1 from 0.667 to 0.808 (+5 TP pairs) but broke a previously correct case (G3).
No threshold changed. The test split had not been touched by then. New worst case is
about 3 x (3 x 83 + 81 + 1) = ~990 reviewer calls; a typical run is about 350 calls, or
about 200K tokens. If the daily quota runs out, the run resumes from cache on a new key.
