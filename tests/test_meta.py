"""The model client: which provider each chat model goes to, what it costs, and what comes back. Offline."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import meta  # noqa: E402


class Route(unittest.TestCase):
    def test_open_weights_go_to_baseten(self):
        self.assertEqual(meta._route(meta.OPEN_MODEL), (meta.BASETEN_URL, "BASETEN_API_KEY"))
        self.assertEqual(meta._route("muse-spark-1.3"), (meta.BASE_URL, "META_API_KEY"))

    def test_cost_of_open_model(self):
        row = {"model": meta.OPEN_MODEL, "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
                                                   "prompt_tokens_details": {"cached_tokens": 0}}}
        self.assertAlmostEqual(meta.cost_of(row), 1.25 + 4.25)


class Chat(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._post, self._log = meta._post_json, meta._log
        meta._log = lambda *a, **k: None

    def tearDown(self):
        meta._post_json, meta._log = self._post, self._log

    def fake(self, content):
        def post(path, payload, timeout=180, base=meta.BASE_URL, key_name="META_API_KEY"):
            self.sent.append((path, payload, base, key_name))
            return {"choices": [{"message": {"content": content, "reasoning_content": "thinking..."}}], "usage": {}}
        meta._post_json = post

    def test_baseten_call_keeps_only_the_answer(self):
        self.fake('<think>hmm</think> {"ok": true}')
        self.assertEqual(meta.chat_json([{"role": "user", "content": "x"}], model=meta.OPEN_MODEL,
                                        reasoning_effort="low"), {"ok": True})
        path, payload, base, key = self.sent[0]
        self.assertEqual((path, base, key), ("/chat/completions", meta.BASETEN_URL, "BASETEN_API_KEY"))
        self.assertEqual(payload["reasoning_effort"], "low")

    def test_meta_call_unchanged(self):
        self.fake("hello")
        self.assertEqual(meta.chat([{"role": "user", "content": "x"}], reasoning_effort="low"), "hello")
        path, payload, base, key = self.sent[0]
        self.assertEqual((base, key), (meta.BASE_URL, "META_API_KEY"))
        self.assertNotIn("reasoning_effort", payload)   # Meta's API isn't sent Baseten's options


if __name__ == "__main__":
    unittest.main()
