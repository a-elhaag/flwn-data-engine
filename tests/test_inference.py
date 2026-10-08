"""The httpx model client against a fake server: request shape, batching and retry rules."""

import unittest
from unittest.mock import patch

import env  # noqa: F401  (sets the environment the app needs to import)
import httpx

from app.clients import inference
from app.clients.resilience import RateLimiter


class FakeFoundry:
    """Answers requests from a script of responses and records what it was asked."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def install(self, test: unittest.TestCase):
        client = httpx.Client(
            base_url="https://foundry.test",
            headers={"api-key": "test-key"},
            transport=httpx.MockTransport(self.handler),
        )
        for patcher in (
            patch("app.clients.inference._http", return_value=client),
            patch("app.clients.inference._limiter", RateLimiter(10_000, 10_000)),
            patch("app.clients.resilience.time.sleep"),
        ):
            test.slept = patcher.start() if "sleep" in str(patcher.attribute) else patcher.start()
            test.addCleanup(patcher.stop)
        return self


def ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def embeddings_for(texts: list[str]) -> httpx.Response:
    # the server may answer out of order; the client must restore input order
    data = [{"index": i, "embedding": [float(len(t))]} for i, t in enumerate(texts)]
    return ok({"data": list(reversed(data))})


class EmbeddingTests(unittest.TestCase):
    def test_batches_of_16_and_keeps_input_order(self):
        texts = [f"{'x' * n}" for n in range(1, 21)]  # 20 texts, lengths 1..20

        def handler(request):
            return embeddings_for(__import__("json").loads(request.content)["input"])

        fake = FakeFoundry()
        fake.handler = lambda request: (fake.requests.append(request), handler(request))[1]
        fake.install(self)
        self.assertEqual(inference.embed_many(texts), [[float(n)] for n in range(1, 21)])
        self.assertEqual(len(fake.requests), 2)  # 16 + 4

    def test_request_shape(self):
        fake = FakeFoundry(embeddings_for(["hi"])).install(self)
        self.assertEqual(inference.embed("hi"), [2.0])
        request = fake.requests[0]
        self.assertEqual(request.url.path, "/models/embeddings")
        self.assertEqual(request.url.params["api-version"], inference.API_VERSION)
        self.assertEqual(request.headers["api-key"], "test-key")
        self.assertEqual(
            __import__("json").loads(request.content),
            {"input": ["hi"], "model": inference.settings.EMBEDDING_DEPLOYMENT},
        )


class ChatTests(unittest.TestCase):
    def test_returns_clean_text(self):
        message = {"choices": [{"message": {"content": "<|START_TEXT|> hello <|END_TEXT|>\n"}}]}
        fake = FakeFoundry(ok(message)).install(self)
        self.assertEqual(inference.chat("memory_steward.compress", "prompt"), "hello")
        body = __import__("json").loads(fake.requests[0].content)
        self.assertEqual(body["messages"], [{"role": "user", "content": "prompt"}])

    def test_a_null_reply_is_an_empty_string_not_a_crash(self):
        FakeFoundry(ok({"choices": [{"message": {"content": None}}]})).install(self)
        self.assertEqual(inference.chat("t", "p"), "")


class RerankTests(unittest.TestCase):
    def test_orders_documents_best_first(self):
        results = {
            "results": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]
        }
        fake = FakeFoundry(ok(results)).install(self)
        ranked = inference.rerank("which db?", ["a", "b", "c"], top_n=2)
        self.assertEqual(ranked, [(2, 0.9), (0, 0.2)])
        request = fake.requests[0]
        self.assertEqual(request.url.path, "/providers/cohere/v2/rerank")
        self.assertNotIn("api-version", request.url.params)

    def test_no_documents_means_no_request(self):
        fake = FakeFoundry(ok({"results": []})).install(self)
        self.assertEqual(inference.rerank("q", []), [])
        self.assertEqual(fake.requests, [])

    def test_top_n_is_capped_at_the_number_of_documents(self):
        fake = FakeFoundry(ok({"results": []})).install(self)
        inference.rerank("q", ["a", "b"], top_n=50)
        self.assertEqual(__import__("json").loads(fake.requests[0].content)["top_n"], 2)


class RetryTests(unittest.TestCase):
    CHAT = {"choices": [{"message": {"content": "fine"}}]}

    def test_retries_rate_limits_and_honours_retry_after(self):
        throttled = httpx.Response(429, headers={"retry-after": "3"})
        fake = FakeFoundry(throttled, ok(self.CHAT)).install(self)
        self.assertEqual(inference.chat("t", "p"), "fine")
        self.assertEqual(len(fake.requests), 2)
        self.slept.assert_called_once_with(3.0)

    def test_gives_up_after_three_attempts_on_server_errors(self):
        fake = FakeFoundry(httpx.Response(503)).install(self)
        with self.assertRaises(httpx.HTTPStatusError):
            inference.chat("t", "p")
        self.assertEqual(len(fake.requests), 3)

    def test_retries_dropped_connections(self):
        fake = FakeFoundry(httpx.ConnectError("refused"), ok(self.CHAT)).install(self)
        self.assertEqual(inference.chat("t", "p"), "fine")
        self.assertEqual(len(fake.requests), 2)

    def test_does_not_retry_a_rejected_request(self):
        fake = FakeFoundry(httpx.Response(401, json={"error": "bad key"})).install(self)
        with self.assertRaises(httpx.HTTPStatusError):
            inference.chat("t", "p")
        self.assertEqual(len(fake.requests), 1)

    def test_a_huge_retry_after_is_capped(self):
        fake = FakeFoundry(
            httpx.Response(429, headers={"retry-after": "86400"}), ok(self.CHAT)
        ).install(self)
        inference.chat("t", "p")
        self.slept.assert_called_once_with(30.0)
        self.assertEqual(len(fake.requests), 2)


if __name__ == "__main__":
    unittest.main()
