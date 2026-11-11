"""AI runs: a paused run and its review decision, service key only."""

import uuid

import signing
from harness import MemoryHarness

from app.api import tokens


class AiRunTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.task = str(uuid.uuid4())
        self.members = {
            "project_id": str(uuid.uuid4()),
            "agent_member_id": str(uuid.uuid4()),
            "requester_member_id": str(uuid.uuid4()),
        }

    def body(self, stage="review", state=None, **overrides):
        return {
            "workspace_id": self.team_a,
            **self.members,
            "stage": stage,
            "state": {"step": "executor"} if state is None else state,
            **overrides,
        }

    def put(self, **kwargs):
        response = self.client.put(f"/ai-runs/{self.task}", json=self.body(**kwargs))
        self.assertEqual(response.status_code, 204, response.text)

    def get(self):
        return self.client.get(f"/ai-runs/{self.task}")

    def review(self, status="approved", reason=None):
        return self.client.patch(
            f"/ai-runs/{self.task}/review", json={"status": status, "reason": reason}
        )

    def test_put_then_get_round_trips_state(self):
        state = {"step": "executor", "plan": ["a", "b"], "attempts": 2}
        self.put(stage="plan", state=state)
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["task_id"], self.task)
        self.assertEqual(body["workspace_id"], self.team_a)
        self.assertEqual(body["stage"], "plan")
        self.assertEqual(body["state"], state)
        self.assertEqual(body["review_status"], "pending")
        self.assertIsNone(body["review_reason"])

    def test_put_again_replaces_and_resets_review(self):
        self.put()
        self.assertEqual(self.review("rejected", "no tests").status_code, 204)
        self.put(state={"step": "retry"})
        body = self.get().json()
        self.assertEqual(body["state"], {"step": "retry"})
        self.assertEqual(body["review_status"], "pending")
        self.assertIsNone(body["review_reason"])

    def test_review_on_plan_stage_is_409(self):
        self.put(stage="plan")
        response = self.review()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "ai_run_not_in_review")
        self.assertEqual(self.get().json()["review_status"], "pending")

    def test_review_approved_and_rejected_are_stored(self):
        self.put()
        self.assertEqual(self.review("approved").status_code, 204)
        self.assertEqual(self.get().json()["review_status"], "approved")
        self.assertEqual(self.review("rejected", "missing docs").status_code, 204)
        body = self.get().json()
        self.assertEqual(body["review_status"], "rejected")
        self.assertEqual(body["review_reason"], "missing docs")

    def test_get_missing_is_404(self):
        response = self.get()
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "ai_run_not_found")

    def test_delete_is_idempotent(self):
        self.put()
        for _ in range(2):
            response = self.client.delete(f"/ai-runs/{self.task}")
            self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.get().status_code, 404)

    def test_state_must_be_an_object(self):
        for state in (["a", "b"], "text", 3):
            with self.subTest(state=state):
                response = self.client.put(f"/ai-runs/{self.task}", json=self.body(state=state))
                self.assertEqual(response.status_code, 422, response.text)

    def claim(self, expected_stage="review"):
        return self.client.post(
            f"/ai-runs/{self.task}/claim", json={"expected_stage": expected_stage}
        )

    def test_claim_moves_stage_to_resuming(self):
        self.put()
        response = self.claim("review")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stage"], "resuming")
        self.assertEqual(self.get().json()["stage"], "resuming")

    def test_second_claim_is_409(self):
        self.put()
        self.assertEqual(self.claim("review").status_code, 200)
        response = self.claim("review")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "ai_run_not_claimable")

    def test_claim_wrong_stage_is_409(self):
        self.put(stage="plan")
        response = self.claim("review")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "ai_run_not_claimable")
        self.assertEqual(self.get().json()["stage"], "plan")

    def test_claim_missing_is_404(self):
        response = self.claim("review")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "ai_run_not_found")

    def test_bearer_token_is_401_on_every_route(self):
        token, _ = signing.mint(self.team_a, tokens.AGENT_SCOPES, "agent", 600, None)
        headers = {"Authorization": f"Bearer {token}", "X-Data-API-Key": ""}
        requests = [
            ("PUT", f"/ai-runs/{self.task}", {"json": self.body()}),
            ("GET", f"/ai-runs/{self.task}", {}),
            ("PATCH", f"/ai-runs/{self.task}/review", {"json": {"status": "approved"}}),
            ("POST", f"/ai-runs/{self.task}/claim", {"json": {"expected_stage": "review"}}),
            ("DELETE", f"/ai-runs/{self.task}", {}),
        ]
        for method, path, kwargs in requests:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, headers=headers, **kwargs)
                self.assertEqual(response.status_code, 401, response.text)
