"""A backend-signed token through the real gate: workspace lock, revocation, assistant and human."""

import time
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import signing
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from factories import JWKS, KEY
from harness import MemoryHarness, unit
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError

from app.api import revocation, tokens
from app.core.config import settings
from app.db.install import grant_app_role

"""Backend-signed tokens (EdDSA, JWKS): claims, audience and revocation."""


OTHER = Ed25519PrivateKey.generate()


def ids(count=4):
    return [str(uuid.uuid4()) for _ in range(count)]


class SignedTokenTests(unittest.TestCase):
    def setUp(self):
        self.ws, self.member, self.human, self.run = ids()
        for name, value in (
            ("AGENT_TOKENS_ENABLED", True),
            ("TOKEN_JWKS", JWKS),
        ):
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        revoked = patch.object(revocation, "is_revoked", return_value=False)
        self.is_revoked = revoked.start()
        self.addCleanup(revoked.stop)

    def sign(self, key=KEY, kid="k1", **overrides):
        now = int(time.time())
        claims = {
            "iss": settings.TOKEN_ISSUER,
            "aud": [settings.TOKEN_AUDIENCE],
            "sub": "someone",
            "ws": self.ws,
            "typ": "human",
            "ctx": "workspace",
            "mem": self.member,
            "scope": "planning:read",
            "iat": now,
            "exp": now + 900,
            "jti": uuid.uuid4().hex,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": kid})

    def test_each_type_roundtrips_with_its_claims(self):
        cases = {
            "human": {},
            "agent": {"typ": "agent", "obo": self.human},
            "run": {"typ": "run", "agent": self.human, "run": self.run, "scope": "atoms:run"},
            "service": {"typ": "service", "sub": "ai-engine", "mem": None},
        }
        for typ, overrides in cases.items():
            with self.subTest(typ=typ):
                claims = tokens.verify(self.sign(**overrides))
                self.assertEqual((claims.typ, claims.workspace_id), (typ, self.ws))
                self.assertEqual(claims.ctx, "workspace")
                self.assertTrue(claims.jti)
        agent = tokens.verify(self.sign(**cases["agent"]))
        self.assertEqual((agent.member_id, agent.obo), (self.member, self.human))
        run = tokens.verify(self.sign(**cases["run"]))
        self.assertEqual((run.atom_id, run.run_id), (self.human, self.run))

    def test_forwarded_service_is_kept(self):
        self.assertEqual(tokens.verify(self.sign(act="ai-engine")).act, "ai-engine")

    def test_claims_must_fit_the_type(self):
        for overrides in (
            {"obo": self.human},  # a human acts for no one
            {"typ": "agent"},  # an assistant must name its human
            {"typ": "run", "agent": self.human},  # a run needs its run id
            {"typ": "service"},  # a service has no member
            {"typ": "robot"},
            {"ctx": "everywhere"},
            {"mem": None},  # a human needs a member
        ):
            with self.subTest(overrides=overrides), self.assertRaises(tokens.TokenError):
                tokens.verify(self.sign(**overrides))

    def test_missing_claims_are_refused(self):
        for name in ("aud", "jti", "typ", "ctx", "ws", "exp", "iat", "iss"):
            with self.subTest(claim=name), self.assertRaises(tokens.TokenError):
                tokens.verify(self.sign(**{name: None}))

    def test_wrong_audience_issuer_key_or_expiry_is_refused(self):
        for overrides in (
            {"aud": ["atoms-service"]},
            {"iss": "someone-else"},
            {"exp": int(time.time()) - 5},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(tokens.TokenError):
                tokens.verify(self.sign(**overrides))
        with self.assertRaises(tokens.TokenError):  # signed by a key the backend does not own
            tokens.verify(self.sign(key=OTHER))
        with self.assertRaises(tokens.TokenError):  # kid names no known key
            tokens.verify(self.sign(kid="rotated-away"))

    def test_revoked_token_is_refused_and_a_down_database_fails_closed(self):
        self.is_revoked.return_value = True
        with self.assertRaisesRegex(tokens.TokenError, "revoked"):
            tokens.verify(self.sign())
        self.is_revoked.side_effect = OperationalError("select", {}, Exception("down"))
        with self.assertRaisesRegex(tokens.TokenError, "unavailable"):
            tokens.verify(self.sign())

    def test_tokens_off_refuses_everything(self):
        with (
            patch.object(settings, "AGENT_TOKENS_ENABLED", False),
            self.assertRaises(tokens.TokenError),
        ):
            tokens.verify(self.sign())


NO_SERVICE_KEY = {"X-Data-API-Key": ""}  # the harness client sends the service key by default


class SignedTokenGateTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        patcher = patch.object(settings, "TOKEN_JWKS", JWKS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.human = self.member(self.team_a)

    def token(self, workspace=None, **overrides) -> tuple[str, str]:
        now = int(time.time())
        jti = uuid.uuid4().hex
        claims = {
            "iss": settings.TOKEN_ISSUER,
            "aud": [settings.TOKEN_AUDIENCE],
            "sub": "someone",
            "ws": workspace or self.team_a,
            "typ": "human",
            "ctx": "workspace",
            "mem": self.human,
            "scope": "memory:read",
            "iat": now,
            "exp": now + 900,
            "jti": jti,
        }
        claims.update(overrides)
        return jwt.encode(claims, KEY, algorithm="EdDSA", headers={"kid": "k1"}), jti

    def get(self, token, workspace=None):
        return self.client.get(
            f"/workspaces/{workspace or self.team_a}/memories",
            headers={**NO_SERVICE_KEY, "Authorization": f"Bearer {token}"},
        )

    def test_token_works_in_its_workspace_only(self):
        token, _ = self.token()
        self.assertEqual(self.get(token).status_code, 200)
        self.assertEqual(self.get(token, self.team_b).status_code, 403)

    def test_missing_scope_is_refused(self):
        token, _ = self.token(scope="planning:read")
        self.assertEqual(self.get(token).status_code, 403)

    def test_revoked_token_stops_at_once_and_only_the_backend_can_revoke(self):
        token, jti = self.token()
        body = {"jti": jti, "expires_at": int(time.time()) + 900}
        self.assertEqual(self.client.post("/auth/revocations", json=body).status_code, 204)
        self.assertEqual(self.client.post("/auth/revocations", json=body).status_code, 204)
        self.assertEqual(self.get(token).status_code, 401)
        other, _ = self.token()
        self.assertEqual(self.get(other).status_code, 200)
        denied = self.client.post("/auth/revocations", json=body, headers=NO_SERVICE_KEY)
        self.assertEqual(denied.status_code, 401)

    def test_expired_entries_are_dropped_on_the_next_revoke(self):
        revocation.revoke("old", int((datetime.now(UTC) - timedelta(hours=1)).timestamp()))
        revocation.revoke("new", int(time.time()) + 900)
        self.assertFalse(revocation.is_revoked("old"))
        self.assertTrue(revocation.is_revoked("new"))

    def test_assistant_stops_when_the_human_it_acts_for_is_suspended(self):
        assistant = self.member(self.team_a)
        owner = self.member(self.team_a)
        token, _ = self.token(typ="agent", mem=assistant, obo=owner)
        self.assertEqual(self.get(token).status_code, 200)
        self.sql("update members set status = 'suspended' where id = :i", i=owner)
        self.assertEqual(self.get(token).status_code, 403)


def resign(payload):
    return jwt.encode(payload, KEY, algorithm="EdDSA", headers={"kid": "k1"})


class AtomTokenTests(unittest.TestCase):
    def setUp(self):
        self.ids = [str(uuid.uuid4()) for _ in range(4)]
        for patcher in (
            patch.object(settings, "TOKEN_JWKS", JWKS),
            patch.object(revocation, "is_revoked", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def mint(self, **overrides):
        values = dict(
            workspace_id=self.ids[0],
            member_id=self.ids[1],
            atom_id=self.ids[2],
            run_id=self.ids[3],
            ctx="workspace",
            subject="atom",
            scopes={"atoms:read"},
            ttl_seconds=900,
        )
        values.update(overrides)
        return signing.mint(**values)

    def test_run_scoped_roundtrip_and_scope_catalog(self):
        token, expires = self.mint(scopes=tokens.ATOM_SCOPES)
        claims = tokens.verify(token)
        self.assertEqual(
            (claims.workspace_id, claims.member_id, claims.atom_id, claims.run_id), tuple(self.ids)
        )
        self.assertEqual(claims.expires_at, expires)
        self.assertEqual(claims.scopes, tokens.ATOM_SCOPES)
        self.assertNotIn("atoms:admin", claims.scopes)

    def test_verify_rejects_signed_invalid_atom_claim_combinations(self):
        token, _ = self.mint()
        payload = jwt.decode(token, options={"verify_signature": False})
        for changes in (
            {"run": None},
            {"agent": None},
            {"mem": None},
            {"scope": "atoms:admin"},
            {"iat": int(time.time()) - 901},
            {"iat": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(tokens.TokenError):
                tokens.verify(resign({**payload, **changes}))

    def test_verify_rejects_a_signed_token_whose_ctx_is_missing_or_changed_to_nonsense(self):
        token, _ = self.mint()
        payload = jwt.decode(token, options={"verify_signature": False})
        for changes in ({"ctx": None}, {"ctx": "admin"}):
            with self.subTest(changes=changes), self.assertRaises(tokens.TokenError):
                tokens.verify(resign({**payload, **changes}))


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
            ctx="workspace",
            scopes={"memory:read"},
            ttl_seconds=600,
            subject="atom",
        )
        kwargs.update(overrides)
        token, _ = signing.mint(**kwargs)
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
        from app.db.atom_access import AtomAccessError, AtomContext, bind_context
        from app.db.session import session_for
        from app.domain.memory.store import MemoryStore

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

    def test_token_ctx_that_disagrees_with_the_atoms_kind_is_refused(self):
        path = f"/workspaces/{self.team_a}/memories"
        self.assertEqual(self.client.get(path, headers=self.atom_headers()).status_code, 200)
        self.assertEqual(
            self.client.get(path, headers=self.atom_headers(ctx="personal")).status_code, 403
        )

    def test_a_run_or_member_that_does_not_belong_to_the_atom_is_refused(self):
        path = f"/workspaces/{self.team_a}/memories"
        other_member = str(
            self.sql(
                "insert into members (workspace_id,type,agent_kind,name) "
                "values (:ws,'AI','atom','Other') returning id",
                ws=self.team_a,
            )[0]["id"]
        )
        other_atom = str(
            self.sql(
                "insert into atoms (workspace_id,member_id,kind,name,model_tier,"
                "max_runs_per_day,max_cost_per_day,max_actions_per_day) "
                "values (:ws,:member,'workspace','Other','small',10,10,10) returning id",
                ws=self.team_a,
                member=other_member,
            )[0]["id"]
        )
        other_run = str(
            self.sql(
                "insert into agent_runs (workspace_id,atom_id,agent_id,trigger,status) "
                "values (:ws,:atom,:member,'schedule','running') returning id",
                ws=self.team_a,
                atom=other_atom,
                member=other_member,
            )[0]["id"]
        )
        for name, overrides in (
            ("run of another atom", {"run_id": other_run}),
            ("member of another atom", {"member_id": other_member}),
            ("atom that is not the member's", {"atom_id": other_atom}),
            ("invented run", {"run_id": str(uuid.uuid4())}),
        ):
            with self.subTest(name):
                headers = self.atom_headers(**overrides)
                self.assertEqual(self.client.get(path, headers=headers).status_code, 403)

    def test_atom_agent_label_comes_from_the_token_and_others_must_supply_one(self):
        from app.integrations.mcp.memory_tools import _agent

        atom = tokens.Claims(
            self.team_a,
            frozenset(),
            "atom",
            0,
            self.member_id,
            self.atom_id,
            self.run_id,
            "workspace",
        )
        human = tokens.Claims(self.team_a, frozenset(), "human", 0, self.member_id)
        self.assertEqual(_agent(atom, "spoofed"), f"atom:{self.atom_id}")
        self.assertEqual(_agent(atom, None), f"atom:{self.atom_id}")
        self.assertEqual(_agent(human, "reviewer"), "reviewer")
        for missing in (None, "", "  "):
            with self.assertRaises(ValueError):
                _agent(human, missing)

    def test_mcp_memory_tools_label_an_atom_from_its_token_even_without_an_agent_argument(self):
        self.sql(
            "insert into atom_grants (workspace_id,atom_id,resource_type,level) "
            "values (:ws,:atom,'memory','write')",
            ws=self.team_a,
            atom=self.atom_id,
        )
        headers = {
            **self.atom_headers(scopes={"memory:read", "memory:write"}),
            "Accept": "application/json, text/event-stream",
        }

        def call(name, arguments):
            response = self.client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["result"]

        for agent in ({}, {"agent": "spoofed display name"}):
            result = call("memory_remember", {"text": "Atom note", "source": "chat", **agent})
            self.assertFalse(result.get("isError"), result)
        labels = {
            row["agent_name"]
            for row in self.sql(
                "select agent_name from memories where owner_member_id=:m", m=self.member_id
            )
        }
        self.assertEqual(labels, {f"atom:{self.atom_id}"})
        recalled = call("memory_recall", {"query": "Atom note"})
        self.assertFalse(recalled.get("isError"), recalled)

    def test_terminal_run_and_suspended_member_stop_existing_bearer(self):
        headers = self.atom_headers()
        path = f"/workspaces/{self.team_a}/memories"
        self.sql("update agent_runs set status='succeeded' where id=:r", r=self.run_id)
        self.assertEqual(self.client.get(path, headers=headers).status_code, 403)
        self.sql("update agent_runs set status='running' where id=:r", r=self.run_id)
        self.sql("update members set status='suspended' where id=:m", m=self.member_id)
        self.assertEqual(self.client.get(path, headers=headers).status_code, 403)


class ServiceKeyOnlyTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        switch = patch.object(settings, "AGENT_TOKENS_ENABLED", False)  # the production default
        switch.start()
        self.addCleanup(switch.stop)

    def test_service_key_works_on_crud_and_memory_routes(self):
        service = {"X-Data-API-Key": "test-data-key"}
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/projects", headers=service).status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/memories", headers=service).status_code, 200
        )

    def test_bearer_tokens_are_refused(self):
        token, _ = self.minted_before_switch()
        response = self.client.get(
            f"/workspaces/{self.team_a}/projects",
            headers={"Authorization": f"Bearer {token}", "X-Data-API-Key": ""},
        )
        self.assertEqual(response.status_code, 401, response.text)

    def minted_before_switch(self):
        """A real token, minted while tokens are on: it must still be refused."""

        with patch.object(settings, "AGENT_TOKENS_ENABLED", True):
            return signing.mint(self.team_a, {"planning:read"}, "x", 60)
