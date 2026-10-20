import uuid

from harness import CrudHarness


class PlanningQueriesTests(CrudHarness):
    def setUp(self):
        super().setUp()
        self.base = f"/workspaces/{self.team_a}"
        self.project = self.client.post(
            f"{self.base}/projects", json={"key": "FLWN", "name": "Flwn"}
        ).json()
        self.item = self.client.post(
            f"{self.base}/projects/{self.project['id']}/work-items",
            json={"title": "w", "type": "FEATURE"},
        ).json()
        self.tasks_url = f"{self.base}/projects/{self.project['id']}/tasks"

    def task(self, **body):
        response = self.client.post(
            self.tasks_url, json={"work_item_id": self.item["id"], "title": "t", **body}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_task_by_key(self):
        first, second = self.task(), self.task()
        found = self.client.get(f"{self.base}/tasks/by-key/FLWN-2")
        self.assertEqual((found.status_code, found.json()["id"]), (200, second["id"]))
        self.assertEqual(found.json()["identifier"], "FLWN-2")
        self.assertEqual(self.client.get(f"{self.base}/tasks/by-key/FLWN-9").status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}/tasks/by-key/OTHER-1").status_code, 404)
        for bad in ("flwn-1", "FLWN-0", "FLWN-01", "FLWN1", "FLWN-"):
            with self.subTest(key=bad):
                self.assertEqual(
                    self.client.get(f"{self.base}/tasks/by-key/{bad}").status_code, 422
                )
        other = self.client.get(f"/workspaces/{self.team_b}/tasks/by-key/FLWN-1")
        self.assertEqual(other.status_code, 404)
        self.assertNotEqual(first["id"], second["id"])

    def test_task_filters(self):
        ada = self.member(self.team_a)
        sprint = self.client.post(
            f"{self.base}/projects/{self.project['id']}/sprints", json={"name": "S1"}
        ).json()
        in_sprint = self.task(sprint_id=sprint["id"], assignee_id=ada)
        backlog = self.task()
        done = self.task(status="done")

        def ids(**params):
            rows = self.client.get(self.tasks_url, params=params).json()["items"]
            return [row["id"] for row in rows]

        self.assertEqual(ids(backlog="true"), [backlog["id"], done["id"]])
        self.assertEqual(ids(backlog="false"), [in_sprint["id"]])
        self.assertEqual(ids(assignee_id=ada), [in_sprint["id"]])
        self.assertEqual(ids(status="done"), [done["id"]])
        self.assertEqual(ids(sprint_id=sprint["id"]), [in_sprint["id"]])
        self.assertEqual(
            self.client.get(self.tasks_url, params={"status": "nope"}).status_code, 422
        )
        self.assertEqual(
            self.client.get(self.tasks_url, params={"assignee_id": "nope"}).status_code, 422
        )

    def test_my_tasks_and_my_projects(self):
        ada, bob = self.member(self.team_a), self.member(self.team_a)
        mine = self.task(assignee_id=ada)
        self.task(assignee_id=bob)
        tasks = self.client.get(f"{self.base}/members/{ada}/tasks").json()["items"]
        self.assertEqual([t["id"] for t in tasks], [mine["id"]])

        team = self.client.post(f"{self.base}/teams", json={"key": "BE", "name": "Backend"}).json()
        self.client.put(f"{self.base}/teams/{team['id']}/members", json={"member_ids": [ada]})
        via_team = self.client.post(
            f"{self.base}/projects", json={"key": "TEAM", "name": "T", "team_id": team["id"]}
        ).json()
        led = self.client.post(
            f"{self.base}/projects", json={"key": "LEAD", "name": "L", "lead_id": bob}
        ).json()
        ada_projects = self.client.get(f"{self.base}/members/{ada}/projects").json()["items"]
        bob_projects = self.client.get(f"{self.base}/members/{bob}/projects").json()["items"]
        self.assertEqual([p["id"] for p in ada_projects], [via_team["id"]])
        self.assertEqual([p["id"] for p in bob_projects], [led["id"]])
        for what in ("tasks", "projects"):
            missing = self.client.get(f"{self.base}/members/{uuid.uuid4()}/{what}")
            self.assertEqual(missing.status_code, 404)
            foreign = self.client.get(f"{self.base}/members/{self.member(self.team_b)}/{what}")
            self.assertEqual(foreign.status_code, 404, "another workspace's member")

    def test_has_more_tells_whether_a_page_follows(self):
        for _ in range(5):
            self.task()
        first = self.client.get(self.tasks_url, params={"limit": 2, "offset": 0}).json()
        self.assertEqual((len(first["items"]), first["has_more"]), (2, True))
        last = self.client.get(self.tasks_url, params={"limit": 2, "offset": 4}).json()
        self.assertEqual(([t["number"] for t in last["items"]], last["has_more"]), ([5], False))
        exact = self.client.get(self.tasks_url, params={"limit": 5}).json()
        self.assertEqual((len(exact["items"]), exact["has_more"]), (5, False))

    def test_user_lookup_by_email_ignores_case(self):
        email = f"{uuid.uuid4().hex[:8]}.Look@Example.com"
        user = self.client.post("/users", json={"email": email, "name": "Lu"}).json()
        self.addCleanup(self.sql, "delete from users where id = :i", i=user["id"])
        found = self.client.get("/users", params={"email": email.upper()}).json()
        self.assertEqual([u["id"] for u in found["items"]], [user["id"]])
        none = self.client.get("/users", params={"email": "nobody@example.com"}).json()
        self.assertEqual(none["items"], [])
