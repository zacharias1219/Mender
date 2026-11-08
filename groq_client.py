"""Groq chat-completions client: retry with backoff, atomic disk cache, validated reads.

Standard library only. The API key is read from GROQ_API_KEY at call time and only ever
placed in the Authorization header: never cached, logged, or put in an error message.
Usage: python groq_client.py --ping [--cache-dir DIR]
"""
import argparse
import datetime
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

API_URL = "https://api.groq.com/openai/v1/chat/completions"
USER_AGENT = "mender-selfrepair/1"  # Cloudflare rejects Python's default UA (error 1010)
MODELS = ("openai/gpt-oss-20b", "openai/gpt-oss-120b")
DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
MAX_RETRIES = 5          # retries after the first attempt, on 429, 5xx, or network error
BACKOFF_BASE = 2.0       # seconds; doubles per retry unless Retry-After says otherwise
BACKOFF_CAP = 60.0
TIMEOUT = 120
ENTRY_FIELDS = ("key", "model", "content", "finish_reason", "model_id", "date")

network_calls = 0        # HTTP requests sent this process (cache hits do not count)
_sleep = time.sleep      # replaced in tests


class GroqError(Exception):
    """A call failed for good: retries exhausted, or a non-retryable HTTP error."""


class GroqBadOutput(GroqError):
    """Groq rejected the model's own reply (JSON mode: code json_validate_failed). Not an API
    failure: callers count it as a bad reply (a failed attempt, or a parse failure)."""


def _post(body, headers):
    """Send one request. Returns (status, response headers, body bytes); never raises on HTTP status."""
    req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def cache_key(model, messages, params):
    blob = json.dumps({"model": model, "params": params, "messages": messages},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_entry(path, key):
    """Return a complete, valid entry or None. Corrupt or partial files are deleted."""
    try:
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        if isinstance(entry, dict) and entry.get("key") == key and all(k in entry for k in ENTRY_FIELDS):
            return entry
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        pass
    try:
        os.remove(path)
    except OSError:
        pass
    return None


def _write_entry(path, entry):
    """Write to a temp file in the same folder, then rename: an entry is complete or absent (G12)."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _request(model, messages, params):
    """One logical call with retry and backoff. Returns (response dict, rate-limit headers)."""
    global network_calls
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise GroqError("GROQ_API_KEY is not set")
    body = json.dumps({"model": model, "messages": messages, **params}).encode("utf-8")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json",
               "Accept": "application/json", "User-Agent": USER_AGENT}
    last = ""
    for attempt in range(MAX_RETRIES + 1):
        network_calls += 1
        try:
            status, rh, raw = _post(body, headers)
        except (urllib.error.URLError, OSError) as e:  # network failure: retryable
            status, rh, raw, last = 0, {}, b"", type(e).__name__
        if status == 200:
            try:
                data = json.loads(raw)
                data["choices"][0]["message"]
            except (ValueError, KeyError, IndexError, TypeError):
                last = "malformed 200 response"
            else:
                rate = {k.lower(): v for k, v in rh.items() if k.lower().startswith("x-ratelimit")}
                return data, rate
        elif status and status != 429 and status < 500:
            err = _error_body(raw)
            cls = GroqBadOutput if err.get("code") == "json_validate_failed" else GroqError
            raise cls(f"HTTP {status} from Groq for {model}: {_safe_error(raw)}")
        else:
            last = last if status == 0 else f"HTTP {status}: {_safe_error(raw)[:120]}"
        if attempt == MAX_RETRIES:
            break
        wait = min(BACKOFF_CAP, BACKOFF_BASE * 2 ** attempt)
        retry_after = {k.lower(): v for k, v in rh.items()}.get("retry-after")
        try:
            wait = max(wait, min(BACKOFF_CAP, float(retry_after)))
        except (TypeError, ValueError):
            pass
        _sleep(wait)
    raise GroqError(f"retries exhausted for {model} after {MAX_RETRIES + 1} attempts ({last})")


def _error_body(raw):
    try:
        err = json.loads(raw)["error"]
        return err if isinstance(err, dict) else {}
    except (ValueError, KeyError, TypeError):
        return {}


def _safe_error(raw):
    """Groq's error message only, trimmed. Never includes request headers, so never the key."""
    try:
        return str(json.loads(raw)["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return raw[:200].decode("utf-8", "replace")


def chat(model, messages, cache_dir=DEFAULT_CACHE, bypass_cache=False, **params):
    """Return a cache entry dict: content, finish_reason, model_id, date, cached, ratelimit, usage.

    bypass_cache=True always calls the API (stability and confirmation reruns) and overwrites
    the entry. If finish_reason is "length", retries once with double max_completion_tokens;
    a second cut-off is returned as is and the caller counts it as a parse failure.
    """
    key = cache_key(model, messages, params)
    path = os.path.join(cache_dir, key[:2], key + ".json")
    if not bypass_cache:
        entry = _read_entry(path, key)
        if entry is not None:
            return {**entry, "cached": True}
    data, rate = _request(model, messages, params)
    if data["choices"][0].get("finish_reason") == "length":
        tok = "max_tokens" if "max_tokens" in params else "max_completion_tokens"
        data, rate = _request(model, messages, {**params, tok: 2 * int(params.get(tok) or 1024)})
    choice = data["choices"][0]
    entry = {
        "key": key, "model": model, "params": params,
        "content": choice["message"].get("content") or "",
        "finish_reason": choice.get("finish_reason"),
        "model_id": data.get("model", model),
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "usage": data.get("usage", {}),
        "ratelimit": rate,
    }
    _write_entry(path, entry)
    return {**entry, "cached": False}


def ping(cache_dir):
    ok = True
    for model in MODELS:
        msgs = [{"role": "user", "content": "Reply with the single word: pong"}]
        try:
            e = chat(model, msgs, cache_dir=cache_dir, temperature=0,
                     max_completion_tokens=256, reasoning_effort="low")
        except GroqError as err:
            print(f"{model}: FAILED {err}")
            ok = False
            continue
        src = "cache hit" if e["cached"] else "network"
        rate = " ".join(f"{k}={v}" for k, v in sorted(e["ratelimit"].items())) or "none"
        print(f"{model}: {src} | model_id={e['model_id']} | finish={e['finish_reason']} | "
              f"reply={e['content'].strip()[:40]!r} | date={e['date']}\n  ratelimit: {rate}")
    print(f"network calls: {network_calls}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ping", action="store_true", help="one call to each model, via the cache")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    a = ap.parse_args()
    if not a.ping:
        ap.print_help()
        sys.exit(2)
    sys.exit(ping(a.cache_dir))
