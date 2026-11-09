"""The data-engine side of the backend auth contract (flwnBackend docs/AUTH.md §5, §6, §12).

Memberships across workspaces, atomic workspace + owner creation, the last-owner rule (service
checks, the database backstop and a real race), ownership transfer, canonical emails and service
key rotation.
"""

import threading
import time
import uuid
from unittest.mock import patch

from harness import CrudHarness
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.api import auth as auth_module
from app.api import tokens as t
from app.core.config import Settings, settings
from app.db.session import workspace_session
from app.domain.identity.members_teams import MemberService

SERVICE = {"Authorization": "", "X-Data-API-Key": "test-data-key"}


class ContractHarness(CrudHarness):
    def setUp(self):
        super().setUp()
        self._workspaces: list[str] = []
        self._users: list[str] = []
        # Registered after the harness's own: runs first, workspaces (and their members) before
        # their users, so no owner is ever deleted from a live workspace.
        self.addCleanup(self._drop_created)

    def _drop_created(self):
        for workspace in self._workspaces:
            self.sql("delete from workspaces where id = :i", i=workspace)
        for user in self._users:
            self.sql("delete from members where user_id = :i", i=user)
            self.sql("delete from users where id = :i", i=user)

    def user(self, email: str | None = None) -> str:
        response = self.client.post(
            "/users", json={"email": email or f"{uuid.uuid4().hex[:10]}@example.com", "name": "U"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self._users.append(response.json()["id"])
        return response.json()["id"]

    def owned_workspace(self, owner: str, name: str = "Acme") -> tuple[str, str]:
        """(workspace id, owner member id) made through the API."""
        response = self.client.post(
            "/workspaces",
            json={"name": name, "slug": f"ws-{uuid.uuid4().hex[:10]}", "owner_user_id": owner},
        )
        self.assertEqual(response.status_code, 201, response.text)
        workspace = response.json()["id"]
        self._workspaces.append(workspace)
        (member,) = self.sql(
            "select id from members where workspace_id = :w and user_id = :u", w=workspace, u=owner
        )
        return workspace, str(member["id"])

    def join(self, workspace: str, user: str, role: str = "member", status: str = "active") -> str:
        (row,) = self.sql(
            "insert into members (workspace_id, user_id, type, role, status)"
            " values (:w, :u, 'HUMAN', :r, :s) returning id",
            w=workspace,
            u=user,
            r=role,
            s=status,
        )
        return str(row["id"])

    def ai(self, workspace: str) -> str:
        (row,) = self.sql(
            "insert into members (workspace_id, type, name, agent_kind)"
            " values (:w, 'AI', 'Bot', 'team_agent') returning id",
            w=workspace,
        )
        return str(row["id"])

    def patch_member(self, workspace: str, member: str, **body):
        return self.client.patch(
            f"/workspaces/{workspace}/members/{member}", json=body, headers=SERVICE
        )

    def role(self, member: str) -> str:
        return self.sql("select role from members where id = :i", i=member)[0]["role"]


class MembershipsTests(ContractHarness):
    def url(self, user, query=""):
        return f"/users/{user}/memberships{query}"

    def test_lists_live_memberships_across_workspaces(self):
        user = self.user()
        first, mine = self.owned_workspace(user, "First")
        other = self.user()
        second, _ = self.owned_workspace(other, "Second")
        suspended = self.join(second, user, "admin", "suspended")
        gone, _ = self.owned_workspace(other, "Gone")
        self.join(gone, user)
        self.client.delete(f"/workspaces/{gone}", headers=SERVICE)
        left, _ = self.owned_workspace(other, "Left")
        self.sql(
            "update members set deleted_at = now() where id = :i", i=self.join(left, user, "admin")
        )

        response = self.client.get(self.url(user))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 2)
        by_ws = {item["workspace_id"]: item for item in body["items"]}
        self.assertEqual(set(by_ws), {first, second})
        self.assertEqual(
            by_ws[first],
            {
                "member_id": mine,
                "workspace_id": first,
                "workspace_name": "First",
                "workspace_slug": by_ws[first]["workspace_slug"],
                "user_id": user,
                "type": "HUMAN",
                "role": "owner",
                "status": "active",
            },
        )
        self.assertEqual(by_ws[second]["member_id"], suspended)

        active = self.client.get(self.url(user, "?status=active&type=HUMAN")).json()
        self.assertEqual([item["workspace_id"] for item in active["items"]], [first])
        self.assertEqual(active["total"], 1)
        self.assertEqual(
            self.client.get(self.url(user, "?type=AI")).json(), {"items": [], "total": 0}
        )

    def test_empty_missing_deleted_and_bad_input(self):
        lonely = self.user()
        self.assertEqual(self.client.get(self.url(lonely)).json(), {"items": [], "total": 0})
        self.assertEqual(self.client.get(self.url(uuid.uuid4())).status_code, 404)
        self.client.delete(f"/users/{lonely}")
        self.assertEqual(self.client.get(self.url(lonely)).status_code, 404)
        self.assertEqual(self.client.get(self.url("not-a-uuid")).status_code, 422)
        self.assertEqual(self.client.get(self.url(lonely, "?status=gone")).status_code, 422)
        self.assertEqual(self.client.get(self.url(lonely, "?type=robot")).status_code, 422)

    def test_service_key_only(self):
        user = self.user()
        token = self.token_headers(self.team_a, t.SCOPE_ADMIN)
        self.assertEqual(self.client.get(self.url(user), headers=token).status_code, 401)
        self.assertEqual(self.client.get(self.url(user), headers=self.NO_TOKEN).status_code, 401)

    def test_function_crosses_workspaces_for_the_app_role_only(self):
        """Under RLS (a non-superuser role, one workspace in scope) the function still sees all."""
        from app.db.install import grant_app_role

        user = self.user()
        first, _ = self.owned_workspace(user)
        second, _ = self.owned_workspace(user)
        role, outsider = f"app_{uuid.uuid4().hex[:8]}", f"out_{uuid.uuid4().hex[:8]}"
        with self.engine.begin() as conn:
            grant_app_role(conn, role)
            conn.exec_driver_sql(f'create role "{outsider}" nologin')
        self.addCleanup(self._drop_roles, role, outsider)

        with workspace_session(self.engine, first) as session:
            session.execute(text(f'set local role "{role}"'))
            seen = session.execute(text("select count(*) from members")).scalar()
            rows = session.execute(
                text("select workspace_id from memberships_for_user(:u)"), {"u": user}
            ).all()
            # app.service was lifted only inside the call
            after = session.execute(text("select current_setting('app.service', true)")).scalar()
        self.assertEqual({str(r[0]) for r in rows}, {first, second})
        self.assertEqual(after, "off")
        self.assertEqual(seen, 1)  # RLS is on: only the first workspace's owner is visible
        with self.assertRaises(DBAPIError) as denied:
            with workspace_session(self.engine, first) as session:
                session.execute(text(f'set local role "{outsider}"'))
                session.execute(text("select * from memberships_for_user(:u)"), {"u": user})
        self.assertIn("permission denied", str(denied.exception))

    def _drop_roles(self, *roles):
        with self.engine.begin() as conn:
            for role in roles:
                conn.exec_driver_sql(f'drop owned by "{role}"')
                conn.exec_driver_sql(f'drop role "{role}"')


class WorkspaceCreateTests(ContractHarness):
    def test_creates_workspace_and_owner_together(self):
        owner = self.user()
        workspace, member = self.owned_workspace(owner)
        (row,) = self.sql("select * from members where id = :i", i=member)
        self.assertEqual(
            (row["type"], row["role"], row["status"], str(row["user_id"])),
            ("HUMAN", "owner", "active", owner),
        )
        self.assertEqual([e["action"] for e in self.events(workspace, "member")], ["created"])

    def test_missing_owner_slug_taken_and_validation(self):
        owner = self.user()
        slug = f"taken-{uuid.uuid4().hex[:8]}"
        body = {"name": "A", "slug": slug, "owner_user_id": owner}
        created = self.client.post("/workspaces", json=body)
        self.assertEqual(created.status_code, 201, created.text)
        self._workspaces.append(created.json()["id"])
        self.assertEqual(self.client.post("/workspaces", json=body).status_code, 409)
        missing = self.client.post(
            "/workspaces", json={**body, "slug": slug + "-x", "owner_user_id": str(uuid.uuid4())}
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        deleted = self.user()
        self.client.delete(f"/users/{deleted}")
        gone = self.client.post(
            "/workspaces", json={**body, "slug": slug + "-z", "owner_user_id": deleted}
        )
        self.assertEqual(gone.status_code, 404, gone.text)
        no_owner = self.client.post("/workspaces", json={"name": "A", "slug": slug + "-y"})
        self.assertEqual(no_owner.status_code, 422)
        self.assertEqual(
            self.sql("select count(*) as n from workspaces where slug like :s", s=slug + "-%")[0][
                "n"
            ],
            0,
        )


class LastOwnerTests(ContractHarness):
    def setUp(self):
        super().setUp()
        self.owner = self.user()
        self.ws, self.owner_member = self.owned_workspace(self.owner)

    def assert_last_owner(self, response):
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json(), {"detail": "last_owner", "code": "last_owner", "fields": None}
        )

    def test_sole_owner_cannot_be_demoted_suspended_or_removed(self):
        self.assert_last_owner(self.patch_member(self.ws, self.owner_member, role="admin"))
        self.assert_last_owner(self.patch_member(self.ws, self.owner_member, status="suspended"))
        self.assert_last_owner(
            self.client.delete(
                f"/workspaces/{self.ws}/members/{self.owner_member}", headers=SERVICE
            )
        )
        self.assert_last_owner(self.client.delete(f"/users/{self.owner}"))
        self.assertEqual(self.role(self.owner_member), "owner")
        # changes that keep the owner are fine
        ok = self.patch_member(self.ws, self.owner_member, settings={"x": 1})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_another_active_owner_makes_it_possible(self):
        second = self.join(self.ws, self.user(), "owner")
        self.assertEqual(
            self.patch_member(self.ws, self.owner_member, role="admin").status_code, 200
        )
        # second is now the only owner again
        self.assert_last_owner(self.patch_member(self.ws, second, role="member"))
        suspended_owner = self.join(self.ws, self.user(), "owner", "suspended")
        self.assert_last_owner(self.patch_member(self.ws, second, role="member"))
        self.patch_member(self.ws, suspended_owner, status="active")
        self.assertEqual(
            self.client.delete(
                f"/workspaces/{self.ws}/members/{second}", headers=SERVICE
            ).status_code,
            204,
        )

    def test_user_delete_checks_every_owned_workspace(self):
        co_owner = self.user()
        self.join(self.ws, co_owner, "owner")
        solo, _ = self.owned_workspace(co_owner)
        self.assert_last_owner(self.client.delete(f"/users/{co_owner}"))
        self.client.delete(f"/workspaces/{solo}", headers=SERVICE)  # a deleted workspace is exempt
        self.assertEqual(self.client.delete(f"/users/{co_owner}").status_code, 204)
        self.assert_last_owner(self.client.delete(f"/users/{self.owner}"))

    def test_ai_members_can_never_own(self):
        bot = self.ai(self.ws)
        response = self.patch_member(self.ws, bot, role="owner")
        self.assertEqual(response.status_code, 422, response.text)
        created = self.client.post(
            f"/workspaces/{self.ws}/members",
            json={"type": "AI", "name": "Bot", "agent_kind": "team_agent", "role": "owner"},
            headers=SERVICE,
        )
        self.assertEqual(created.status_code, 422, created.text)
        with self.assertRaises(IntegrityError):
            self.sql("update members set role = 'owner' where id = :i", i=bot)

    def test_database_backstop_and_deleted_workspace_exemption(self):
        with self.assertRaises(IntegrityError) as raised:
            self.sql("update members set role = 'admin' where id = :i", i=self.owner_member)
        self.assertIn("last_owner", str(raised.exception))
        with self.assertRaises(IntegrityError):
            self.sql("delete from members where id = :i", i=self.owner_member)
        self.assertEqual(self.role(self.owner_member), "owner")
        # a legacy workspace with no owner is not blocked by unrelated changes
        legacy = self.workspace("legacy")
        plain = self.member(legacy)
        self.assertEqual(self.patch_member(legacy, plain, role="viewer").status_code, 200)
        # once the workspace is deleted, its owners may go
        self.sql("update workspaces set deleted_at = now() where id = :i", i=self.ws)
        self.sql("update members set role = 'admin' where id = :i", i=self.owner_member)
        self.assertEqual(self.role(self.owner_member), "admin")

    def test_concurrent_demotions_of_the_last_two_owners(self):
        second = self.join(self.ws, self.user(), "owner")
        barrier = threading.Barrier(2)
        results = {}

        def demote(member):
            barrier.wait()
            results[member] = self.patch_member(self.ws, member, role="admin").status_code

        # Hold the owner locks so both requests are in flight before either can proceed.
        with workspace_session(self.engine, "", service=True) as blocker:
            blocker.execute(
                text(
                    "select id from members where workspace_id = :w and role = 'owner' for update"
                ),
                {"w": self.ws},
            )
            threads = [
                threading.Thread(target=demote, args=(m,)) for m in (self.owner_member, second)
            ]
            for thread in threads:
                thread.start()
            time.sleep(0.5)  # both requests are now waiting on the owner locks
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(results.values()), [200, 409], results)
        owners = self.sql(
            "select count(*) as n from members where workspace_id = :w and role = 'owner'",
            w=self.ws,
        )
        self.assertEqual(owners[0]["n"], 1)


class OwnershipTransferTests(ContractHarness):
    def setUp(self):
        super().setUp()
        self.ws, self.owner = self.owned_workspace(self.user())
        self.target = self.join(self.ws, self.user())

    def transfer(self, actor, **body):
        headers = {**SERVICE, **({"X-Acting-Member-Id": actor} if actor else {})}
        return self.client.post(
            f"/workspaces/{self.ws}/ownership/transfer", json=body, headers=headers
        )

    def test_transfer_and_step_down(self):
        response = self.transfer(self.owner, to_member_id=self.target, demote_self_to="admin")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["workspace_id"], self.ws)
        self.assertEqual(
            (body["from_member"]["id"], body["from_member"]["role"]), (self.owner, "admin")
        )
        self.assertEqual(
            (body["to_member"]["id"], body["to_member"]["role"]), (self.target, "owner")
        )
        # the old owner no longer may
        self.assertEqual(self.transfer(self.owner, to_member_id=self.owner).status_code, 403)
        self.assertEqual(
            [e["action"] for e in self.events(self.ws, "member")][-2:],
            ["ownership_transferred", "ownership_transferred"],
        )

    def test_keep_both_owners(self):
        response = self.transfer(self.owner, to_member_id=self.target, demote_self_to=None)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((self.role(self.owner), self.role(self.target)), ("owner", "owner"))

    def test_actor_must_be_owner(self):
        self.assertEqual(self.transfer(None, to_member_id=self.target).status_code, 403)
        admin = self.join(self.ws, self.user(), "admin")
        self.assertEqual(self.transfer(admin, to_member_id=self.target).status_code, 403)
        self.assertEqual(self.role(self.target), "member")

    def test_invalid_targets(self):
        suspended = self.join(self.ws, self.user(), status="suspended")
        _, outsider = self.owned_workspace(self.user())
        for target in (self.ai(self.ws), suspended, outsider, str(uuid.uuid4()), self.owner):
            with self.subTest(target=target):
                response = self.transfer(self.owner, to_member_id=target, demote_self_to="member")
                self.assertEqual(response.status_code, 422, response.text)
        bad = self.transfer(self.owner, to_member_id=self.target, demote_self_to="viewer")
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.role(self.owner), "owner")


class CanonicalEmailApiTests(ContractHarness):
    def test_users_are_stored_and_found_canonically(self):
        local = uuid.uuid4().hex[:8]
        user = self.user(f"  {local.upper()}+Tag@Bücher.Example ")
        stored = self.client.get(f"/users/{user}").json()["email"]
        self.assertEqual(stored, f"{local}+tag@xn--bcher-kva.example")
        found = self.client.get("/users", params={"email": f"{local.upper()}+TAG@bücher.example"})
        self.assertEqual([u["id"] for u in found.json()["items"]], [user])
        self.assertEqual(self.client.get("/users", params={"email": "bogus"}).json()["items"], [])
        clash = self.client.post(
            "/users", json={"email": f"{local}+tag@xn--bcher-kva.example", "name": "Other"}
        )
        self.assertEqual(
            (clash.status_code, clash.json()),
            (409, {"detail": "email_taken", "code": "email_taken", "fields": None}),
        )
        other = self.user()
        taken = self.client.patch(
            f"/users/{other}", json={"email": f"{local.upper()}+tag@Bücher.example"}
        )
        self.assertEqual(
            (taken.status_code, taken.json()),
            (409, {"detail": "email_taken", "code": "email_taken", "fields": None}),
        )
        self.assertEqual(
            self.client.post("/users", json={"email": "a@localhost", "name": "X"}).status_code, 422
        )

    def test_nfc_and_member_emails(self):
        decomposed = f"É{uuid.uuid4().hex[:6]}@Example.com"
        user = self.user(decomposed)
        composed = "é" + decomposed[2:].lower()
        self.assertEqual(self.client.get(f"/users/{user}").json()["email"], composed)
        member = self.client.post(
            f"/workspaces/{self.team_a}/members",
            json={"type": "HUMAN", "name": "Elodie", "email": composed.upper()},
            headers=SERVICE,
        )
        self.assertEqual(member.status_code, 201, member.text)
        self.assertEqual(member.json()["user_id"], user)  # linked, not a second account
        # a person's email is on the user account, so the member route refuses to change it (#33)
        other = self.member(self.team_a)
        refused = self.patch_member(self.team_a, other, email=decomposed)
        self.assertEqual(refused.status_code, 422, refused.text)


class MemberEmailRaceTests(ContractHarness):
    def test_losing_the_create_race_for_a_new_email_is_email_taken(self):
        # Two calls add the same new person at once: the second insert hits uq_users_email.
        clash = IntegrityError(
            "insert",
            {},
            Exception('duplicate key value violates unique constraint "uq_users_email"'),
        )
        ws, _ = self.owned_workspace(self.user())
        with patch.object(MemberService, "create", side_effect=clash):
            response = self.client.post(
                f"/workspaces/{ws}/members",
                json={
                    "type": "HUMAN",
                    "role": "member",
                    "name": "Race",
                    "email": "race@example.com",
                },
            )
        self.assertEqual(
            (response.status_code, response.json()),
            (409, {"detail": "email_taken", "code": "email_taken", "fields": None}),
        )


class ServiceKeyTests(ContractHarness):
    def test_next_key_is_accepted_during_rotation(self):
        with patch.object(settings, "DATA_API_KEY_NEXT", "next-data-key"):
            for key in ("test-data-key", "next-data-key"):
                response = self.client.get("/users", headers={"X-Data-API-Key": key})
                self.assertEqual(response.status_code, 200, key)
            self.assertEqual(
                self.client.get("/users", headers={"X-Data-API-Key": "nope"}).status_code, 401
            )
        self.assertEqual(
            self.client.get("/users", headers={"X-Data-API-Key": "next-data-key"}).status_code, 401
        )

    def setUp(self):
        super().setUp()
        auth_module._reject_log.clear()  # the per-source rate limit is process-wide

    def test_rejected_key_is_logged_with_source_never_the_key(self):
        with self.assertLogs("app.api.auth", level="WARNING") as logs:
            self.client.get("/users", headers={"X-Data-API-Key": "wrong-secret-value"})
        (line,) = logs.output
        self.assertIn("peer=", line)
        self.assertIn("path=/users", line)
        self.assertNotIn("wrong-secret-value", line)

    def test_rejection_log_cannot_be_injected_and_names_the_forwarded_client(self):
        with self.assertLogs("app.api.auth", level="WARNING") as logs:
            self.client.get(
                "/users",
                headers={"X-Data-API-Key": "bad", "X-Forwarded-For": "203.0.113.9\tFAKE"},
            )
        (line,) = logs.output
        self.assertNotIn("\t", line)  # control characters are escaped, never written raw
        self.assertIn("forwarded_for=203.0.113.9\\tFAKE", line)
        self.assertEqual(auth_module._clean("a\nb\x1b"), "a\\nb\\x1b")  # newlines too

    def test_rejections_from_one_source_log_once_a_minute(self):
        with self.assertLogs("app.api.auth", level="WARNING") as logs:
            for _ in range(5):
                self.client.get("/users", headers={"X-Data-API-Key": "bad"})
            # the window passes: the next warning reports what was suppressed
            source, (last, suppressed) = next(iter(auth_module._reject_log.items()))
            auth_module._reject_log[source] = (last - 61, suppressed)
            self.client.get("/users", headers={"X-Data-API-Key": "bad"})
        self.assertEqual(len(logs.output), 2)
        self.assertIn("suppressed=4", logs.output[1])

    def test_next_key_is_checked_at_start_up(self):
        base = {
            "DATA_API_KEY": "current-key-value",
            "AZURE_FOUNDRY_ENDPOINT": "https://x.invalid",
            "AZURE_FOUNDRY_KEY": "k",
        }
        for bad in ("current-key-value", "short", " padded-next-key-value ", "   "):
            with self.subTest(next_key=bad):
                with self.assertRaises(ValueError):
                    Settings(_env_file=None, **base, DATA_API_KEY_NEXT=bad)
        Settings(_env_file=None, **base, DATA_API_KEY_NEXT="another-key-value")  # fine
        Settings(_env_file=None, **base)  # rotation off
