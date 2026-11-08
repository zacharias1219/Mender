"""Offline tests for groq_client: no network, no real key."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import groq_client as gc  # noqa: E402

FAKE_KEY = "fake-test-key-0123456789abcdefghij"
MSGS = [{"role": "user", "content": "hi"}]


def ok(content="pong", finish="stop"):
    body = {"model": "openai/gpt-oss-20b",
            "choices": [{"message": {"content": content}, "finish_reason": finish}]}
    return 200, {"x-ratelimit-remaining-requests": "999"}, json.dumps(body).encode()


class FakePost:
    """Replays queued responses and records every request sent."""

    def __init__(self, *responses):
        self.responses, self.sent = list(responses), []

    def __call__(self, body, headers):
        self.sent.append((json.loads(body), headers))
        return self.responses.pop(0)


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sleeps = []
        for p in (mock.patch.dict(os.environ, {"GROQ_API_KEY": FAKE_KEY}),
                  mock.patch.object(gc, "_sleep", self.sleeps.append)):
            p.start()
            self.addCleanup(p.stop)

    def use(self, *responses):
        fake = FakePost(*responses)
        p = mock.patch.object(gc, "_post", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def call(self, **kw):
        return gc.chat("openai/gpt-oss-20b", MSGS, cache_dir=self.dir, **kw)

    def files(self):
        return [os.path.join(r, f) for r, _, fs in os.walk(self.dir) for f in fs]

    def test_429_then_success(self):
        fake = self.use((429, {"retry-after": "3"}, b'{"error":{"message":"slow down"}}'), ok())
        e = self.call()
        self.assertEqual(e["content"], "pong")
        self.assertEqual(len(fake.sent), 2)
        self.assertEqual(self.sleeps, [3.0])  # Retry-After wins over the 2 s base backoff

    def test_5xx_exhausts_retries(self):
        fake = self.use(*[(503, {}, b"down")] * (gc.MAX_RETRIES + 1))
        with self.assertRaises(gc.GroqError) as cm:
            self.call()
        self.assertIn("retries exhausted", str(cm.exception))
        self.assertEqual(len(fake.sent), gc.MAX_RETRIES + 1)
        self.assertEqual(self.files(), [])  # nothing cached on failure

    def test_4xx_is_not_retried(self):
        fake = self.use((401, {}, b'{"error":{"message":"Invalid API Key"}}'))
        with self.assertRaises(gc.GroqError) as cm:
            self.call()
        self.assertEqual(len(fake.sent), 1)
        self.assertNotIn(FAKE_KEY, str(cm.exception))

    def test_json_validate_failed_is_cached_as_empty_reply_not_retried(self):
        body = b'{"error":{"message":"Failed to validate JSON","code":"json_validate_failed"}}'
        fake = self.use((400, {}, body))
        e = self.call()
        self.assertEqual((e["content"], e["finish_reason"]), ("", "json_validate_failed"))
        self.assertTrue(self.call()["cached"])  # replay makes no second call
        self.assertEqual(len(fake.sent), 1)

    def test_cache_hit_makes_no_call(self):
        fake = self.use(ok())
        self.assertFalse(self.call()["cached"])
        e = self.call()
        self.assertTrue(e["cached"])
        self.assertEqual(len(fake.sent), 1)

    def test_bypass_cache_calls_again(self):
        fake = self.use(ok("a"), ok("b"))
        self.call()
        self.assertEqual(self.call(bypass_cache=True)["content"], "b")
        self.assertEqual(self.call()["content"], "b")  # rerun result replaces the entry
        self.assertEqual(len(fake.sent), 2)

    def test_truncated_cache_file_is_discarded_and_refetched(self):
        fake = self.use(ok("first"), ok("second"))
        self.call()
        (path,) = self.files()
        with open(path, "r+", encoding="utf-8") as f:
            f.truncate(len(f.read()) // 2)
        e = self.call()
        self.assertEqual((e["content"], e["cached"]), ("second", False))
        self.assertEqual(len(fake.sent), 2)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["content"], "second")

    def test_finish_reason_length_retried_once_with_double_tokens(self):
        fake = self.use(ok("cut", "length"), ok("full"))
        e = self.call(max_completion_tokens=100)
        self.assertEqual(e["content"], "full")
        self.assertEqual([b["max_completion_tokens"] for b, _ in fake.sent], [100, 200])

    def test_finish_reason_length_twice_is_returned_not_looped(self):
        fake = self.use(ok("cut", "length"), ok("cut again", "length"))
        e = self.call(max_completion_tokens=100)
        self.assertEqual(e["finish_reason"], "length")
        self.assertEqual(len(fake.sent), 2)

    def test_key_never_in_cache_entry(self):
        fake = self.use(ok())
        self.call()
        self.assertEqual(fake.sent[0][1]["Authorization"], "Bearer " + FAKE_KEY)
        for path in self.files():
            with open(path, encoding="utf-8") as f:
                self.assertNotIn(FAKE_KEY, f.read())

    def test_custom_user_agent_sent(self):
        fake = self.use(ok())
        self.call()
        self.assertEqual(fake.sent[0][1]["User-Agent"], gc.USER_AGENT)

    def test_missing_key_fails_before_any_call(self):
        fake = self.use()
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(gc.GroqError):
            self.call()
        self.assertEqual(fake.sent, [])


if __name__ == "__main__":
    unittest.main()
