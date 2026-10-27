"""The Atomizer service forwards the backend's signed human token; atoms admin accepts it with no service key."""

import time
import uuid
from unittest.mock import patch

import jwt
import test_atoms_api
from harness import MemoryHarness
from sqlalchemy import create_engine, event
from test_signed_tokens import JWKS, KEY

from app.config import settings
from app.db.install import grant_app_role


class SignedAtomAdminTests(MemoryHarness):
    mcp = test_atoms_api.AtomApiTests.mcp
    token = test_atoms_api.AtomApiTests.token  # service-minted (legacy) tokens for setup helpers
    create_atom = test_atoms_api.AtomApiTests.create_atom
    active_atom = test_atoms_api.AtomApiTests.active_atom

    def setUp(self):
        super().setUp()
        with self.engine.begin() as conn:
            grant_app_role(conn, "flwn_atom_signed_test")
        application_engine = create_engine(self.engine.url)

        @event.listens_for(application_engine, "connect")
        def restricted_connection(connection, _):
            connection.execute("set role flwn_atom_signed_test")
            connection.commit()

        self.addCleanup(application_engine.dispose)
        engine_patch = patch("app.db.session.engine", return_value=application_engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        self.owner = self.member(self.team_a)
        self.addCleanup(self.sql, "delete from workspaces where id=:id", id=self.team_a)
        self.sql("update members set role='owner' where id=:id", id=self.owner)
        self.admin = self.token(member_id=self.owner, scopes=["atoms:admin"])
        self.base = f"/workspaces/{self.team_a}/atoms"
        patcher = patch.object(settings, "TOKEN_JWKS", JWKS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def headers(self, member, scope="atoms:admin"):
        now = int(time.time())
        claims = {
            "iss": settings.TOKEN_ISSUER,
            "aud": ["atoms-service", settings.TOKEN_AUDIENCE],  # the Atomizer forwards it on
            "sub": member,
            "ws": self.team_a,
            "typ": "human",
            "ctx": "workspace",
            "mem": member,
            "scope": scope,
            "iat": now,
            "exp": now + 900,
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(claims, KEY, algorithm="EdDSA", headers={"kid": "k1"})
        return {"X-Data-API-Key": "", "Authorization": f"Bearer {token}"}

    def test_admin_member_manages_atoms_as_themselves(self):
        headers = self.headers(self.owner)
        listed = self.client.get(self.base, headers=headers)
        self.assertEqual(listed.status_code, 200, listed.text)

    def test_header_cannot_change_who_acts(self):
        other = self.member(self.team_a)
        headers = {**self.headers(self.owner), "X-Acting-Member-Id": other}
        body = {
            "name": "Mine",
            "model_tier": "small",
            "max_runs_per_day": 1,
            "max_cost_per_day": "1.000000",
            "max_actions_per_day": 1,
        }
        created = self.client.post(self.base, headers=headers, json=body)
        self.assertEqual(created.status_code, 201, created.text)

    def test_other_scope_and_scheduler_routes_are_refused(self):
        weak = self.headers(self.owner, scope="atoms:read")
        self.assertEqual(self.client.get(self.base, headers=weak).status_code, 403)
        strong = self.headers(self.owner)
        self.assertEqual(
            self.client.get(self.base + "/scheduler-state", headers=strong).status_code, 403
        )

    def test_session_route_accepts_a_good_token_and_refuses_a_revoked_one(self):
        headers = self.headers(self.owner)
        ok = self.client.get("/auth/session", headers=headers)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["member_id"], self.owner)
        jwt_token = headers["Authorization"][7:]
        jti = jwt.decode(jwt_token, options={"verify_signature": False})["jti"]
        body = {"jti": jti, "expires_at": int(time.time()) + 900}
        self.assertEqual(self.client.post("/auth/revocations", json=body).status_code, 204)
        self.assertEqual(self.client.get("/auth/session", headers=headers).status_code, 401)
        self.assertEqual(
            self.client.get("/auth/session", headers={"X-Data-API-Key": ""}).status_code, 401
        )

    def test_scheduler_workspaces_lists_only_workspaces_with_work(self):
        self.assertNotIn(
            self.team_a, self.client.get("/atoms/scheduler-workspaces").json()["workspace_ids"]
        )
        self.active_atom()
        found = self.client.get("/atoms/scheduler-workspaces").json()["workspace_ids"]
        self.assertIn(self.team_a, found)
        self.assertNotIn(self.team_b, found)
        denied = self.client.get("/atoms/scheduler-workspaces", headers=self.headers(self.owner))
        self.assertEqual(denied.status_code, 401)

    def test_whole_deploy_flow_runs_on_the_forwarded_token_alone(self):
        h = self.headers(self.owner)  # what the Atomizer sends: no service key, no acting header
        body = {
            "name": "Mail",
            "model_tier": "small",
            "max_runs_per_day": 5,
            "max_cost_per_day": "2.000000",
            "max_actions_per_day": 50,
        }
        atom = self.client.post(self.base, headers=h, json=body)
        self.assertEqual(atom.status_code, 201, atom.text)
        path = f"{self.base}/{atom.json()['id']}"
        steps = [
            ("patch", "PATCH", path, {"description": "Daily mail"}, 200),
            ("version", "POST", path + "/versions", {"instructions": "Read mail."}, 201),
            ("grant", "PUT", path + "/grants", {"resource_type": "memory", "level": "read"}, 200),
            (
                "connection",
                "PUT",
                path + "/connections",
                {
                    "toolkit": "outlook",
                    "composio_user_id": "u1",
                    "toolkit_version": "1.0.0",
                    "permission_ceiling": "read",
                    "allowed_tools": ["OUTLOOK_LIST_MESSAGES"],
                },
                200,
            ),
            ("schedule", "POST", path + "/schedules", {"interval_minutes": 60}, 201),
        ]
        out = {}
        for name, method, url, payload, expected in steps:
            response = self.client.request(method, url, headers=h, json=payload)
            self.assertEqual(response.status_code, expected, f"{name}: {response.text}")
            out[name] = response.json()
        activated = self.client.post(
            path + "/activate", headers=h, json={"version_id": out["version"]["id"]}
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(self.client.get(path, headers=h).json()["status"], "active")
        self.assertIn(
            atom.json()["id"], [a["id"] for a in self.client.get(self.base, headers=h).json()]
        )
        paused = self.client.patch(path, headers=h, json={"status": "paused"})
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertEqual(paused.json()["status"], "paused")
        killed = self.client.patch(path, headers=h, json={"status": "killed"})
        self.assertEqual(killed.json()["status"], "killed")
        self.assertEqual(
            self.client.patch(path, headers=h, json={"status": "paused"}).status_code, 422
        )
        draft = self.client.post(self.base, headers=h, json={**body, "name": "Scratch"}).json()
        self.assertEqual(
            self.client.delete(f"{self.base}/{draft['id']}", headers=h).status_code, 204
        )

    def test_plain_member_gets_only_their_own_personal_atom(self):
        member = self.member(self.team_a)
        self.addCleanup(self.sql, "delete from atoms where owner_member_id=:id", id=member)
        h = self.headers(member)
        body = {
            "name": "Mine",
            "kind": "personal",
            "owner_member_id": member,
            "model_tier": "small",
            "max_runs_per_day": 999,
            "max_cost_per_day": "99.000000",
            "max_actions_per_day": 999,
        }
        created = self.client.post(self.base, headers=h, json=body)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(
            created.json()["max_runs_per_day"], 10
        )  # caps forced to the member default
        workspace_atom = self.client.post(
            self.base, headers=h, json={**body, "kind": "workspace", "owner_member_id": None}
        )
        self.assertEqual(workspace_atom.status_code, 403, workspace_atom.text)

    def test_forwarded_token_searches_and_writes_skills_with_the_skill_scopes(self):
        h = self.headers(self.owner, scope="atoms:admin skills:read skills:write")
        written = self.mcp(
            "skill_write",
            {
                "name": "Mail triage",
                "description": "Sort mail",
                "when_to_use": "daily mail",
                "instructions": "Read, then summarize.",
                "scope": "workspace",
            },
            h,
        )
        self.assertFalse(written.get("isError"), written)
        found = self.mcp("skills_search", {"query": "mail"}, h)
        self.assertFalse(found.get("isError"), found)
        self.assertTrue(found["structuredContent"]["skills"])
        no_skill_scope = self.mcp("skills_search", {"query": "mail"}, self.headers(self.owner))
        self.assertTrue(no_skill_scope.get("isError"), no_skill_scope)

    def test_mcp_atom_admin_tools_mirror_the_rest_routes(self):
        h = self.headers(self.owner)
        listed = self.client.post(
            "/mcp",
            headers={**h, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        ).json()["result"]["tools"]
        names = {tool["name"] for tool in listed}
        self.assertTrue(
            {"atom_create", "atom_activate", "atom_schedule_set", "atom_delete"} <= names
        )

        def call(name, **arguments):
            result = self.mcp(name, arguments, h)
            self.assertFalse(result.get("isError"), (name, result))
            return result["structuredContent"]

        atom = call(
            "atom_create",
            body={
                "name": "Mcp mail",
                "model_tier": "small",
                "max_runs_per_day": 5,
                "max_cost_per_day": "2.000000",
                "max_actions_per_day": 50,
            },
        )
        aid = atom["id"]
        version = call("atom_version_create", atom_id=aid, body={"instructions": "Read mail."})
        call("atom_grant_set", atom_id=aid, body={"resource_type": "memory", "level": "read"})
        connection = call(
            "atom_connection_set",
            atom_id=aid,
            body={
                "toolkit": "outlook",
                "composio_user_id": "u1",
                "toolkit_version": "1.0.0",
                "permission_ceiling": "read",
                "allowed_tools": ["OUTLOOK_LIST_MESSAGES"],
            },
        )
        schedule = call("atom_schedule_set", atom_id=aid, body={"interval_minutes": 60})
        replaced = call(
            "atom_schedule_set",
            atom_id=aid,
            schedule_id=schedule["id"],
            body={"interval_minutes": 30},
        )
        self.assertEqual(replaced["id"], schedule["id"])
        call("atom_activate", atom_id=aid, version_id=version["id"])
        self.assertEqual(call("atom_get", atom_id=aid)["status"], "active")
        self.assertIn(aid, [a["id"] for a in call("atom_list")["atoms"]])
        self.assertEqual(
            call("atom_configure", atom_id=aid, body={"status": "paused"})["status"], "paused"
        )
        call("atom_connection_delete", atom_id=aid, connection_id=connection["id"])
        call("atom_schedule_delete", atom_id=aid, schedule_id=schedule["id"])
        call("atom_configure", atom_id=aid, body={"status": "killed"})
        self.assertEqual(call("atom_delete", atom_id=aid), {"deleted": True})

    def test_mcp_atom_admin_needs_the_admin_scope_and_a_human_token(self):
        weak = self.headers(self.owner, scope="memory:read")
        refused = self.mcp("atom_list", {}, weak)
        self.assertTrue(refused.get("isError"), refused)
        run = self.token(member_id=self.owner, scopes=["atoms:read"])
        self.assertTrue(self.mcp("atom_list", {}, run).get("isError"))

    def test_mcp_admin_attaches_and_toggles_a_skill(self):
        h = self.headers(self.owner, scope="atoms:admin skills:write")
        skill = self.mcp(
            "skill_write",
            {
                "name": "Triage",
                "description": "Sort",
                "when_to_use": "daily",
                "instructions": "Read, then summarize.",
                "scope": "workspace",
            },
            h,
        )["structuredContent"]
        atom = self.mcp(
            "atom_create",
            {
                "body": {
                    "name": "Skilled",
                    "model_tier": "small",
                    "max_runs_per_day": 5,
                    "max_cost_per_day": "2.000000",
                    "max_actions_per_day": 50,
                }
            },
            h,
        )["structuredContent"]
        attached = self.mcp(
            "atom_skill_attach",
            {
                "atom_id": atom["id"],
                "body": {"skill_id": skill["id"], "skill_version": skill["version"]},
            },
            h,
        )
        self.assertFalse(attached.get("isError"), attached)
        attachment = attached["structuredContent"]
        toggled = self.mcp(
            "atom_skill_enable",
            {"atom_id": atom["id"], "attachment_id": attachment["id"], "body": {"enabled": False}},
            h,
        )
        self.assertFalse(toggled.get("isError"), toggled)
        no_scope = self.mcp(
            "atom_skill_attach",
            {
                "atom_id": atom["id"],
                "body": {"skill_id": skill["id"], "skill_version": skill["version"]},
            },
            self.headers(self.owner, scope="skills:write"),
        )
        self.assertTrue(no_scope.get("isError"), no_scope)
