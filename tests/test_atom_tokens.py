"""Atom JWT invariants and existing REST paths with restricted PostgreSQL connections."""

import time
import unittest
import uuid
from unittest.mock import patch

import jwt
from harness import TOKEN_SECRET, MemoryHarness, unit
from sqlalchemy import create_engine, event

from app.api import tokens
from app.config import settings
from app.db.install import grant_app_role


class AtomTokenTests(unittest.TestCase):
    def setUp(self):
        self.ids = [str(uuid.uuid4()) for _ in range(4)]
        self.secret = patch.object(settings, "MEMORY_TOKEN_SECRET", TOKEN_SECRET)
        self.secret.start()
        self.addCleanup(self.secret.stop)

    def mint(self, **overrides):
        values = dict(
            workspace_id=self.ids[0],
            member_id=self.ids[1],
            atom_id=self.ids[2],
            run_id=self.ids[3],
            subject="atom",
            scopes={"atoms:read"},
            ttl_seconds=900,
        )
        values.update(overrides)
        return tokens.mint(**values)

    def test_run_scoped_roundtrip_and_scope_catalog(self):
        token, expires = self.mint(scopes=tokens.ATOM_SCOPES)
        claims = tokens.verify(token)
        self.assertEqual(
            (claims.workspace_id, claims.member_id, claims.atom_id, claims.run_id), tuple(self.ids)
        )
        self.assertEqual(claims.expires_at, expires)
        self.assertEqual(claims.scopes, tokens.ATOM_SCOPES)
        self.assertNotIn("atoms:admin", claims.scopes)

    def test_atom_claims_are_complete_short_lived_and_never_admin(self):
        for values in (
            {"member_id": None},
            {"atom_id": None},
            {"run_id": None},
            {"ttl_seconds": 901},
            {"ttl_seconds": 0},
            {"scopes": {"atoms:admin"}},
        ):
            with self.subTest(values=values), self.assertRaises(tokens.TokenError):
                self.mint(**values)

    def test_verify_rejects_signed_invalid_atom_claim_combinations(self):
        token, _ = self.mint()
        payload = jwt.decode(
            token, TOKEN_SECRET, algorithms=["HS256"], options={"verify_exp": False}
        )
        for changes in (
            {"run_id": None},
            {"atom_id": None},
            {"mem": None},
            {"scope": "atoms:admin"},
            {"iat": int(time.time()) - 901},
            {"iat": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(tokens.TokenError):
                tokens.verify(jwt.encode({**payload, **changes}, TOKEN_SECRET, algorithm="HS256"))

    def test_existing_nonatom_tokens_remain_compatible(self):
        token, _ = self.mint(
            atom_id=None,
            run_id=None,
            member_id=None,
            scopes={"memory:read", "atoms:admin"},
            ttl_seconds=3600,
        )
        claims = tokens.verify(token)
        self.assertIsNone(claims.atom_id)
        self.assertIsNone(claims.member_id)


class AtomRestAccessTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        with self.engine.begin() as conn:
            grant_app_role(conn, "flwn_atom_rest_test")
        self.restricted_engine = create_engine(self.engine.url)

        @event.listens_for(self.restricted_engine, "connect")
        def restrict(dbapi_connection, _):
            with dbapi_connection.cursor() as cursor:
                cursor.execute("set role flwn_atom_rest_test")
            dbapi_connection.commit()

        self.addCleanup(self.restricted_engine.dispose)
        engine_patch = patch("app.db.session.engine", return_value=self.restricted_engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        self.member_id = str(
            self.sql(
                "insert into members (workspace_id,type,agent_kind,name) "
                "values (:ws,'AI','atom','Atom') returning id",
                ws=self.team_a,
            )[0]["id"]
        )
        self.atom_id = str(
            self.sql(
                "insert into atoms (workspace_id,member_id,kind,name,model_tier,"
                "max_runs_per_day,max_cost_per_day,max_actions_per_day) "
                "values (:ws,:member,'workspace','Atom','small',10,10,10) returning id",
                ws=self.team_a,
                member=self.member_id,
            )[0]["id"]
        )
        version = self.sql(
            "insert into atom_versions (workspace_id,atom_id,version,instructions,source,created_by) "
            "values (:ws,:atom,1,'test','human',:member) returning id",
            ws=self.team_a,
            atom=self.atom_id,
            member=self.member_id,
        )[0]["id"]
        self.sql(
            "update atoms set status='active',active_version_id=:v where id=:a",
            v=version,
            a=self.atom_id,
        )
        self.run_id = str(
            self.sql(
                "insert into agent_runs (workspace_id,atom_id,agent_id,trigger,status) "
                "values (:ws,:atom,:member,'schedule','running') returning id",
                ws=self.team_a,
                atom=self.atom_id,
                member=self.member_id,
            )[0]["id"]
        )
        self.memory_id = self.remember(self.team_a)

    def atom_headers(self, **overrides):
        kwargs = dict(
            workspace_id=self.team_a,
            member_id=self.member_id,
            atom_id=self.atom_id,
            run_id=self.run_id,
            scopes={"memory:read"},
            ttl_seconds=600,
            subject="atom",
        )
        kwargs.update(overrides)
        token, _ = tokens.mint(**kwargs)
        return {"X-Data-API-Key": "", "Authorization": f"Bearer {token}"}

    def test_atom_remember_defaults_are_private_and_audited(self):
        self.sql(
            "insert into atom_grants (workspace_id,atom_id,resource_type,level) "
            "values (:ws,:atom,'memory','write')",
            ws=self.team_a,
            atom=self.atom_id,
        )
        headers = self.atom_headers(scopes={"memory:read", "memory:write"})
        body = {"text": "Atom private finding", "source": "chat", "agent": "spoofed display name"}
        response = self.client.post(
            f"/workspaces/{self.team_a}/memories", json=body, headers=headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        memory_id = response.json()["point_id"]
        row = self.sql(
            "select scope,owner_member_id,created_by from memories where id=:id", id=memory_id
        )[0]
        self.assertEqual(row["scope"], "agent")
        self.assertEqual(str(row["owner_member_id"]), self.member_id)
        self.assertEqual(str(row["created_by"]), self.member_id)
        audit = self.sql(
            "select actor_id from events where entity_id=:id and action='created'", id=memory_id
        )
        self.assertEqual([str(row["actor_id"]) for row in audit], [self.member_id])

    @patch.object(settings, "MEMORY_DEDUP_THRESHOLD", 0.9)
    def test_atom_memory_writes_require_live_write_grant_even_for_dedup(self):
        self.sql(
            "update memories set embedding=cast(:vector as vector) where id=:id",
            vector=str(unit()),
            id=self.memory_id,
        )
        path = f"/workspaces/{self.team_a}/memories"
        headers = self.atom_headers(scopes={"memory:write"})
        body = {"text": "Private memory", "source": "chat", "agent": "atom"}
        self.assertEqual(self.client.post(path, json=body, headers=headers).status_code, 403)
        grant = self.sql(
            "insert into atom_grants (workspace_id,atom_id,resource_type,level) "
            "values (:ws,:atom,'memory','write') returning id",
            ws=self.team_a,
            atom=self.atom_id,
        )[0]["id"]
        result = self.client.post(path, json=body, headers=headers)
        self.assertEqual(result.status_code, 200, result.text)
        memory_id = result.json()["point_id"]
        duplicate = self.client.post(path, json=body, headers=headers)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json(), {"point_id": memory_id, "deduplicated": True})
        for level in ("read", "summary"):
            self.sql("update atom_grants set level=:level where id=:id", id=grant, level=level)
            self.assertEqual(self.client.post(path, json=body, headers=headers).status_code, 403)
        self.sql("delete from atom_grants where id=:id", id=grant)
        self.assertEqual(self.client.post(path, json=body, headers=headers).status_code, 403)
        self.assertEqual(
            len(self.sql("select id from memories where workspace_id=:ws", ws=self.team_a)), 2
        )
        self.assertEqual(
            len(self.sql("select id from events where entity_id=:id", id=memory_id)), 2
        )

    def test_atom_batch_and_direct_store_force_identity_without_changing_human_defaults(self):
        from app.atoms.access import AtomAccessError, AtomContext, bind_context
        from app.db.session import session_for
        from app.memory.store import MemoryStore

        grant = self.sql(
            "insert into atom_grants (workspace_id,atom_id,resource_type,level) "
            "values (:ws,:atom,'memory','write') returning id",
            ws=self.team_a,
            atom=self.atom_id,
        )[0]["id"]
        body = {"items": [{"text": "Batch atom note", "source": "chat", "agent": "atom"}]}
        response = self.client.post(
            f"/workspaces/{self.team_a}/memories/batch",
            json=body,
            headers=self.atom_headers(scopes={"memory:write"}),
        )
        self.assertEqual(response.status_code, 200, response.text)
        context = AtomContext(self.team_a, self.atom_id, self.run_id, self.member_id)
        values = dict(
            text="Explicit spoof rejected by normalization",
            raw_text=None,
            source="chat",
            agent="other",
            importance=3,
            embedding=[0.0] * 1536,
            embedding_model="test",
            now=time.time(),
            created_by=str(uuid.uuid4()),
            owner_member_id=uuid.uuid4(),
            scope="workspace",
        )
        # Explicit service-side checks must still deny when the connection itself bypasses RLS.
        with bind_context(context), patch("app.db.session.engine", return_value=self.engine):
            with session_for(self.team_a) as session:
                row = MemoryStore(session, self.team_a).insert(**values)
                self.assertEqual(
                    (row.scope, str(row.owner_member_id), str(row.created_by)),
                    ("agent", self.member_id, self.member_id),
                )
        self.sql("delete from atom_grants where id=:id", id=grant)
        with bind_context(context), patch("app.db.session.engine", return_value=self.engine):
            with self.assertRaises(AtomAccessError), session_for(self.team_a) as session:
                MemoryStore(session, self.team_a).insert(**values)
        ordinary = self.sql(
            "select scope,owner_member_id from memories where id=:id", id=self.memory_id
        )[0]
        self.assertEqual(ordinary["scope"], "workspace")
        self.assertIsNone(ordinary["owner_member_id"])

    def test_existing_browse_path_obeys_live_grants_and_resets_request_context(self):
        headers = self.atom_headers()
        path = f"/workspaces/{self.team_a}/memories"
        response = self.client.get(path, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"], [])
        grant = self.sql(
            "insert into atom_grants (workspace_id,atom_id,resource_type,level) "
            "values (:ws,:atom,'memory','read') returning id",
            ws=self.team_a,
            atom=self.atom_id,
        )[0]["id"]
        response = self.client.get(path, headers=headers)
        self.assertEqual([item["id"] for item in response.json()["items"]], [self.memory_id])
        self.sql("delete from atom_grants where id=:g", g=grant)
        self.assertEqual(self.client.get(path, headers=headers).json()["items"], [])
        self.assertEqual(len(self.client.get(path).json()["items"]), 1)
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_b}/memories", headers=headers).status_code, 403
        )

    def test_mint_requires_matching_run_and_member_and_rejects_plain_atom_member(self):
        body = dict(
            workspace_id=self.team_a,
            member_id=self.member_id,
            atom_id=self.atom_id,
            run_id=self.run_id,
            ttl_seconds=900,
            scopes=["atoms:read"],
        )
        response = self.client.post("/auth/tokens", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(
            "/auth/tokens", json={k: v for k, v in body.items() if k != "ttl_seconds"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = jwt.decode(response.json()["token"], TOKEN_SECRET, algorithms=["HS256"])
        self.assertEqual(payload["exp"] - payload["iat"], 900)
        for changes in (
            {"run_id": str(uuid.uuid4())},
            {"ttl_seconds": 901},
            {"scopes": ["atoms:admin"]},
            {"atom_id": None, "run_id": None},
        ):
            response = self.client.post("/auth/tokens", json={**body, **changes})
            self.assertEqual(response.status_code, 422, response.text)
        headers = self.atom_headers(atom_id=None, run_id=None)
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/memories", headers=headers).status_code, 403
        )

    def test_terminal_run_and_suspended_member_stop_existing_bearer(self):
        headers = self.atom_headers()
        path = f"/workspaces/{self.team_a}/memories"
        self.sql("update agent_runs set status='succeeded' where id=:r", r=self.run_id)
        self.assertEqual(self.client.get(path, headers=headers).status_code, 403)
        self.sql("update agent_runs set status='running' where id=:r", r=self.run_id)
        self.sql("update members set status='suspended' where id=:m", m=self.member_id)
        self.assertEqual(self.client.get(path, headers=headers).status_code, 403)
