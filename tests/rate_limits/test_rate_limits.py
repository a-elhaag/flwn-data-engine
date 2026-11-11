"""Rate-limit counters: hits per bucket and key hash, a sliding window, service key only."""

import hashlib
import uuid

import signing
from harness import MemoryHarness

from app.api import tokens


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class RateLimitTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.key = digest(f"{uuid.uuid4().hex}@example.com")

    def body(self, bucket="login_fail", key=None, window=3600):
        return {"bucket": bucket, "key_hash": key or self.key, "window_seconds": window}

    def hit(self, **kwargs):
        response = self.client.post("/rate-limits/hit", json=self.body(**kwargs))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["count"]

    def count(self, **kwargs):
        response = self.client.post("/rate-limits/count", json=self.body(**kwargs))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["count"]

    def test_hit_counts_up_within_window(self):
        self.assertEqual([self.hit() for _ in range(3)], [1, 2, 3])

    def test_count_does_not_add_a_hit(self):
        self.hit()
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.hit(), 2)

    def test_old_hits_fall_out_of_the_window(self):
        self.sql(
            "insert into rate_hits (bucket, key_hash, hit_at)"
            " values ('login_fail', :k, now() - interval '2 hours')",
            k=self.key,
        )
        self.assertEqual(self.count(window=3600), 0)
        self.assertEqual(self.count(window=3 * 3600), 1)

    def test_reset_clears_one_key_only(self):
        other = digest("someone-else")
        self.hit()
        self.hit(key=other)
        response = self.client.post("/rate-limits/reset", json=self.body())
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count(key=other), 1)

    def test_buckets_are_separate(self):
        self.hit(bucket="login_fail")
        self.assertEqual(self.count(bucket="magic"), 0)
        self.assertEqual(self.count(bucket="login_fail"), 1)

    def test_bad_key_hash_is_422(self):
        response = self.client.post("/rate-limits/count", json=self.body(key="not-a-hash"))
        self.assertEqual(response.status_code, 422, response.text)

    def test_bearer_token_is_401_on_every_route(self):
        token, _ = signing.mint(self.team_a, tokens.AGENT_SCOPES, "agent", 600, None)
        headers = {"Authorization": f"Bearer {token}", "X-Data-API-Key": ""}
        requests = [
            ("POST", "/rate-limits/count", {"json": self.body()}),
            ("POST", "/rate-limits/hit", {"json": self.body()}),
            ("POST", "/rate-limits/reset", {"json": self.body()}),
        ]
        for method, path, kwargs in requests:
            with self.subTest(path=path):
                response = self.client.request(method, path, headers=headers, **kwargs)
                self.assertEqual(response.status_code, 401, response.text)
