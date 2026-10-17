"""The full journey in one test: checks that the pieces work together."""

import factories as f


def test_full_journey(api):
    # 1-3. user, lookup by email (case-insensitive), organization with its owner
    ahmed = f.make_user(api, name="Ahmed", email="Ahmed.Flow@Flwn.io")
    found = api.get("/users", params={"email": "AHMED.FLOW@FLWN.IO"}).json()["items"]
    assert [u["id"] for u in found] == [ahmed["id"]]
    created = api.post("/organizations", json={"name": "Flwn Inc", "createdById": ahmed["id"]})
    assert created.status_code == 201
    body = created.json()
    assert body["member"]["role"] == "OWNER" and body["member"]["name"] == "Ahmed"
    org = f.Org(body["organization"]["id"], body["member"]["id"], ahmed["id"], "Ahmed", ahmed["email"])

    # 4-5. departments, team, an AI member, both on the team
    software = f.make_workspace(api, org, name="Software")
    backend = f.make_team(api, org, software, name="Backend")
    ghost = f.make_ai_member(api, org, name="Ghost")
    assert f.add_team_member(api, org, backend, org.owner_id, "LEAD")["role"] == "LEAD"
    assert f.add_team_member(api, org, backend, ghost["id"])["role"] == "MEMBER"

    # 6. project, its own team, and a team from another department
    flwn = f.make_project(api, org, software, key="FLWN", name="Flwn")
    f.add_team_to_project(api, org, flwn, backend)
    hr = f.make_workspace(api, org, name="HR")
    recruiting = f.make_team(api, org, hr, name="Recruiting")
    f.add_team_to_project(api, org, flwn, recruiting)

    # 7-9. sprint, work item, three tasks
    sprint = f.make_sprint(api, org, flwn, backend, name="Sprint 1")
    login = f.make_work_item(api, org, flwn, title="Login")
    t1 = f.make_task(api, org, flwn, sprintId=sprint["id"], workItemId=login["id"])
    t2 = f.make_task(api, org, flwn, assigneeId=ghost["id"])
    t3 = f.make_task(api, org, flwn)
    assert [(t["number"], t["key"]) for t in (t1, t2, t3)] == [(1, "FLWN-1"), (2, "FLWN-2"), (3, "FLWN-3")]

    # 10-11. by key, backlog
    assert api.get("/tasks/by-key/FLWN-2", org).json()["id"] == t2["id"]
    backlog = api.get(f"/projects/{flwn['id']}/tasks", org, params={"backlog": "true"}).json()
    assert [t["number"] for t in backlog["items"]] == [2, 3]

    # 12. DONE with and without completedAt
    done = api.patch(f"/tasks/{t3['id']}", org, json={"status": "DONE", "completedAt": "2026-10-17T10:00:00Z"})
    assert done.status_code == 200 and done.json()["status"] == "DONE"
    rejected = api.patch(f"/tasks/{t2['id']}", org, json={"status": "DONE"})
    assert (rejected.status_code, rejected.json()["field"]) == (422, "completedAt")

    # 13. my tasks / my projects
    mine = api.get(f"/members/{ghost['id']}/tasks", org).json()["items"]
    assert [t["id"] for t in mine] == [t2["id"]]
    projects = api.get(f"/members/{org.owner_id}/projects", org).json()["items"]
    assert [p["key"] for p in projects] == ["FLWN"]

    # 14-15. deleting a sprint / work item keeps the task
    assert api.delete(f"/sprints/{sprint['id']}", org).status_code == 204
    assert api.get(f"/tasks/{t1['id']}", org).json()["sprintId"] is None
    assert api.delete(f"/work-items/{login['id']}", org).status_code == 204
    assert api.get(f"/tasks/{t1['id']}", org).json()["workItemId"] is None

    # 16. archive the project: still readable, hidden by archived=false
    assert api.delete(f"/projects/{flwn['id']}", org).status_code == 204
    assert api.get(f"/projects/{flwn['id']}", org).json()["archivedAt"] is not None
    active = api.get(f"/workspaces/{software['id']}/projects", org, params={"archived": "false"}).json()
    assert flwn["id"] not in [p["id"] for p in active["items"]]

    # 17. pagination over the (archived) project's tasks
    for _ in range(3):
        f.make_task(api, org, flwn)
    page = api.get(f"/projects/{flwn['id']}/tasks", org, params={"limit": 2, "offset": 0}).json()
    assert len(page["items"]) == 2 and page["hasMore"] is True
    last = api.get(f"/projects/{flwn['id']}/tasks", org, params={"limit": 2, "offset": 4}).json()
    assert [t["number"] for t in last["items"]] == [5, 6] and last["hasMore"] is False
