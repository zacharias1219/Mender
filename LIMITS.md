# Groq free-tier limits (BUILD_STEPS Step 1, check 4)

Values marked *header* were read from the `x-ratelimit-*` response headers on
2026-11-08 (`python groq_client.py --ping`). Groq reports requests per **day** and tokens
per **minute** in those headers. Richard fills in the blanks from console.groq.com
(Settings, Limits), since the API doesn't report them.

| Limit | openai/gpt-oss-20b (reviewer) | openai/gpt-oss-120b (generator, repair) |
|---|---|---|
| Requests / minute (RPM) | ___ (console) | ___ (console) |
| Requests / day (RPD) | 1,000 (header) | 1,000 (header) |
| Tokens / minute (TPM) | 8,000 (header) | 8,000 (header) |
| Tokens / day (TPD) | ___ (console) | ___ (console) |

## Fit against ARCHITECTURE.md section 8

| Phase | Calls | Model | Fits RPD? |
|---|---|---|---|
| Data generation | ~90 plus retries | 120b | yes |
| 8 rounds x 3 candidates, worst case | ~1,700 | 20b | **no**: 1,000/day |
| 8 rounds, repair | 24 | 120b | yes |

- **Requests:** the worst case needs about 1.7 days of 20b requests. Short-circuiting and the
  cache usually cut this a lot, and a run can resume from cache the next day (abort rule 4).
- **Tokens per minute probably bind first.** A tiny ping used about 330 tokens with
  reasoning. A reviewer call (full prompt plus ad plus reasoning) is estimated at about
  2,000 tokens, which allows about 4 calls per minute, so about 7 minutes per 27-ad split.
  Step 3 measures real `usage` per call (it's stored in every cache entry) and replaces
  this estimate.
- **Tokens per day may be the real ceiling.** Once TPD is known:
  calls per day ≈ TPD / tokens per call. If that is below the run's call count, lower
  `ROUNDS` or `CANDIDATES` in `run.py` and record the new worst case here, before any full
  run (BUILD_STEPS rule: never after seeing results).

## Decision

ROUNDS = 8, CANDIDATES = 3 for now. To be revisited when TPD and the measured tokens per
call are in.
