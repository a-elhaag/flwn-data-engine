"""Drive a running data engine the way its callers do: the backend, humans, the AI engine and atoms.

    python scripts/simulate.py keygen KEYFILE        print the JWKS to set as TOKEN_JWKS
    python scripts/simulate.py run KEYFILE BASE_URL  run every scenario, then delete what it made

The backend's signing key is KEYFILE; the engine must trust its JWKS. DATA_API_KEY is read from
the environment or .env. Everything is created in one throwaway workspace and removed at the end.
"""

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm

ISS, AUD, KID = "flwn-backend", "data-engine", "sim1"
MCP = {"Accept": "application/json, text/event-stream"}


def keygen(path: str) -> None:
    key = Ed25519PrivateKey.generate()
    Path(path).write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    jwk = json.loads(OKPAlgorithm.to_jwk(key.public_key()))
    print(json.dumps({"keys": [{**jwk, "kid": KID, "alg": "EdDSA", "use": "sig"}]}))


def env_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for line in Path(".env").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    sys.exit(f"{name} is not set")


class Sim:
    def __init__(self, key_file: str, base: str):
        self.key = serialization.load_pem_private_key(Path(key_file).read_bytes(), None)
        self.http = httpx.Client(base_url=base.rstrip("/"), timeout=120)
        self.service = {"X-Data-API-Key": env_value("DATA_API_KEY")}
        self.ws = ""
        self.results: list[tuple[bool, str]] = []

    # --- the backend signs tokens; this is its side ------------------------------------------
    def token(
        self,
        typ="human",
        scopes=(),
        mem=None,
        obo=None,
        agent=None,
        run=None,
        ctx="workspace",
        ttl=600,
        **extra,
    ):
        now = int(time.time())
        claims = {
            "iss": ISS,
            "aud": [AUD],
            "sub": extra.pop("sub", "sim"),
            "ws": self.ws,
            "typ": typ,
            "ctx": ctx,
            "scope": " ".join(sorted(scopes)),
            "iat": now,
            "exp": now + ttl,
            "jti": uuid.uuid4().hex,
            **extra,
        }
        for name, value in (("mem", mem), ("obo", obo), ("agent", agent), ("run", run)):
            if value:
                claims[name] = value
        token = jwt.encode(claims, self.key, algorithm="EdDSA", headers={"kid": KID})
        return {"Authorization": f"Bearer {token}"}, claims["jti"], claims["exp"]

    def call(self, label, method, path, expect, headers=None, **kwargs):
        response = self.http.request(
            method, path, headers=self.service if headers is None else headers, **kwargs
        )
        expected = (expect,) if isinstance(expect, int) else tuple(expect)
        ok = response.status_code in expected
        self.results.append(
            (ok, f"{label}: {method} {path.split('?')[0]} -> {response.status_code}")
        )
        if not ok:
            print(
                f"  FAIL {label}: {method} {path} -> {response.status_code} {response.text[:200]}"
            )
        try:
            return response.json() if response.content else None
        except ValueError:
            return None

    def check(self, label, condition):
        self.results.append((bool(condition), f"{label}"))
        if not condition:
            print(f"  FAIL {label}")

    def mcp(self, label, headers, name, arguments=None):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        response = self.http.post("/mcp", headers={**headers, **MCP}, json=body)
        result = response.json().get("result", {}) if response.status_code == 200 else {}
        ok = response.status_code == 200 and not result.get("isError")
        self.results.append((ok, f"{label}: mcp {name}"))
        if not ok:
            print(f"  FAIL {label}: mcp {name} -> {response.status_code} {response.text[:200]}")
        if "structuredContent" in result:
            return result["structuredContent"]
        content = result.get("content") or []
        return json.loads(content[0]["text"]) if content and ok else None

    # --- scenarios ----------------------------------------------------------------------------
    def backend_setup(self):
        tag = uuid.uuid4().hex[:8]
        ws = self.call(
            "create workspace",
            "POST",
            "/workspaces",
            201,
            json={"name": f"zz sim {tag}", "slug": f"zz-sim-{tag}"},
        )
        self.ws = ws["id"]
        self.base = f"/workspaces/{self.ws}"
        self.users = []
        self.members = {}
        for role, name in (("owner", "Olive Owner"), ("member", "Mo Member")):
            user = self.call(
                f"create user {role}",
                "POST",
                "/users",
                201,
                json={"email": f"sim-{role}-{tag}@example.com", "name": name},
            )
            self.users.append(user)
            member = self.call(
                f"add {role}",
                "POST",
                f"{self.base}/members",
                201,
                json={"type": "HUMAN", "role": role, "name": name, "email": user["email"]},
            )
            self.members[role] = member["id"]
        self.call("user by email", "GET", f"/users?email={self.users[0]['email']}", 200)
        self.call("list users", "GET", "/users", 200)
        self.call("list workspaces", "GET", "/workspaces", 200)
        self.call("list members", "GET", f"{self.base}/members", 200)
        assistant = self.call(
            "add assistant member",
            "POST",
            f"{self.base}/members",
            201,
            json={
                "type": "AI",
                "role": "member",
                "name": "Sim Assistant",
                "agent_kind": "team_agent",
            },
        )
        self.members["assistant"] = assistant["id"]
        team = self.call(
            "create team",
            "POST",
            f"{self.base}/teams",
            201,
            json={"key": "SIM", "name": "Sim team", "visibility": "workspace"},
        )
        self.team = team["id"]
        self.call(
            "team members",
            "PUT",
            f"{self.base}/teams/{self.team}/members",
            (200, 204),
            json={"member_ids": [self.members["owner"], self.members["member"]]},
        )
        self.call("readiness", "GET", "/readyz", 200)

    def humans(self):
        owner, _, _ = self.token(
            "human", {"admin"}, mem=self.members["owner"], sub=self.users[0]["id"]
        )
        member, _, _ = self.token(
            "human",
            {
                "planning:read",
                "planning:write",
                "collab:read",
                "collab:write",
                "memory:read",
                "identity:read",
            },
            mem=self.members["member"],
            sub=self.users[1]["id"],
        )
        self.owner_h, self.member_h = owner, member
        self.call("session check", "GET", "/auth/session", 200, headers=owner)

        project = self.call(
            "create project",
            "POST",
            f"{self.base}/projects",
            201,
            headers=owner,
            json={"key": "SIM", "name": "Sim project", "team_id": self.team},
        )
        pid = project["id"]
        item = self.call(
            "create work item",
            "POST",
            f"{self.base}/projects/{pid}/work-items",
            201,
            headers=member,
            json={"title": "Ship sim", "type": "FEATURE"},
        )
        self.call(
            "create sprint",
            "POST",
            f"{self.base}/projects/{pid}/sprints",
            201,
            headers=owner,
            json={"name": "Sprint 1"},
        )
        task = self.call(
            "create task",
            "POST",
            f"{self.base}/projects/{pid}/tasks",
            201,
            headers=member,
            json={
                "work_item_id": item["id"],
                "title": "Write sim",
                "assignee_id": self.members["member"],
            },
        )
        self.call(
            "update task",
            "PATCH",
            f"{self.base}/projects/{pid}/tasks/{task['id']}",
            200,
            headers=member,
            json={"status": "in_progress"},
        )
        self.call(
            "update project",
            "PATCH",
            f"{self.base}/projects/{pid}",
            200,
            headers=owner,
            json={"status": "in_progress"},
        )
        for what in (
            "projects",
            f"projects/{pid}/work-items",
            f"projects/{pid}/sprints",
            f"projects/{pid}/tasks",
            "teams",
            "members",
        ):
            self.call(f"list {what}", "GET", f"{self.base}/{what}", 200, headers=member)
        self.call("member cannot manage atoms", "GET", f"{self.base}/atoms", 403, headers=member)
        self.call(
            "member cannot write identity",
            "POST",
            f"{self.base}/teams",
            403,
            headers=member,
            json={"key": "NO", "name": "No"},
        )

        doc = self.call(
            "create doc",
            "POST",
            f"{self.base}/docs",
            201,
            headers=member,
            json={"title": "Sim doc", "project_id": pid},
        )
        self.call(
            "add block",
            "POST",
            f"{self.base}/docs/{doc['id']}/blocks",
            201,
            headers=member,
            json={"type": "paragraph", "content": {"text": "hello"}},
        )
        self.call("list blocks", "GET", f"{self.base}/docs/{doc['id']}/blocks", 200, headers=member)
        channel = self.call(
            "create channel",
            "POST",
            f"{self.base}/channels",
            201,
            headers=member,
            json={"type": "team", "name": "sim", "team_id": self.team},
        )
        self.call(
            "send message",
            "POST",
            f"{self.base}/channels/{channel['id']}/messages",
            201,
            headers=member,
            json={"body": "hello team", "kind": "text"},
        )
        self.call(
            "list messages",
            "GET",
            f"{self.base}/channels/{channel['id']}/messages",
            200,
            headers=member,
        )
        self.call(
            "comment",
            "POST",
            f"{self.base}/comments",
            201,
            headers=member,
            json={"target_type": "task", "target_id": task["id"], "body": "looks good"},
        )
        self.call(
            "own member row",
            "GET",
            f"{self.base}/members/{self.members['member']}",
            200,
            headers=member,
        )

        meeting = self.call(
            "create meeting",
            "POST",
            f"{self.base}/meetings",
            201,
            headers=owner,
            json={"title": "Sim sync"},
        )
        self.call(
            "invite participant",
            "POST",
            f"{self.base}/meetings/{meeting['id']}/participants",
            (200, 201),
            headers=owner,
            json={"member_ids": [self.members["member"]]},
        )
        self.call("list meetings", "GET", f"{self.base}/meetings", 200, headers=owner)
        self.call("files list", "GET", f"{self.base}/files", (200, 503), headers=owner)

    def ai_engine(self):
        engine, _, _ = self.token(
            "service",
            {
                "memory:read",
                "memory:write",
                "conflicts:read",
                "conflicts:write",
                "meetings:read",
                "files:read",
                "planning:read",
            },
            sub="ai-engine",
        )
        assistant, _, _ = self.token(
            "agent",
            {"memory:read", "memory:write", "planning:read", "collab:read"},
            mem=self.members["assistant"],
            obo=self.members["owner"],
            sub="assistant",
        )
        self.engine_h = engine
        remembered = self.call(
            "remember",
            "POST",
            f"{self.base}/memories",
            200,
            headers=engine,
            json={
                "text": "We chose Postgres with pgvector for memory.",
                "source": "decision",
                "agent": "planner",
            },
        )
        point = remembered["point_id"]
        self.call(
            "remember batch",
            "POST",
            f"{self.base}/memories/batch",
            200,
            headers=engine,
            json={
                "items": [{"text": "Sprints run two weeks.", "source": "chat", "agent": "planner"}]
            },
        )
        found = self.call(
            "recall",
            "POST",
            f"{self.base}/memories/recall",
            200,
            headers=engine,
            json={"query": "which database holds memory?", "agent": "planner", "limit": 5},
        )
        self.check("recall finds the memory", any(m.get("id") == point for m in (found or [])))
        self.call(
            "pin memory",
            "PUT",
            f"{self.base}/memories/{point}/pin",
            200,
            headers=engine,
            json={"pinned": True},
        )
        self.call("memory stats", "GET", f"{self.base}/memories/stats", 200, headers=engine)
        self.call(
            "assistant recalls",
            "POST",
            f"{self.base}/memories/recall",
            200,
            headers=assistant,
            json={"query": "database", "agent": "assistant", "limit": 3},
        )
        self.call(
            "assistant cannot administer", "GET", f"{self.base}/atoms", 403, headers=assistant
        )
        tools = self.http.post(
            "/mcp",
            headers={**engine, **MCP},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        names = {t["name"] for t in tools.json()["result"]["tools"]}
        self.check("mcp lists memory tools", {"memory_recall", "memory_remember"} <= names)
        self.mcp(
            "mcp remember",
            engine,
            "memory_remember",
            {"text": "The engine runs on Container Apps.", "source": "chat", "agent": "planner"},
        )
        self.mcp(
            "mcp recall",
            engine,
            "memory_recall",
            {"query": "where does the engine run?", "agent": "planner"},
        )
        conflict = self.call(
            "flag conflict",
            "POST",
            f"{self.base}/decision-conflicts",
            201,
            headers=engine,
            json={
                "decision_id": point,
                "proposal": "Move memory to Redis.",
                "explanation": "Contradicts the Postgres decision.",
            },
        ) or {"id": str(uuid.uuid4())}  # a failed flag fails the next steps cleanly
        self.call(
            "engine cannot resolve",
            "PUT",
            f"{self.base}/decision-conflicts/{conflict['id']}/resolution",
            403,
            headers=engine,
            json={"status": "dismissed"},
        )
        self.call(
            "backend resolves conflict",
            "PUT",
            f"{self.base}/decision-conflicts/{conflict['id']}/resolution",
            200,
            headers={**self.service, "X-Acting-Member-Id": self.members["owner"]},
            json={"status": "dismissed", "note": "withdrawn"},
        )
        self.call(
            "engine cannot clean up",
            "POST",
            f"{self.base}/memories/cleanup",
            403,
            headers=engine,
            json={},
        )
        self.call(
            "backend dry-run cleanup",
            "POST",
            f"{self.base}/memories/cleanup",
            200,
            json={"retention_days": 30, "dry_run": True},
        )

    def atoms(self):
        admin = self.owner_h
        a = f"{self.base}/atoms"
        atom = self.call(
            "create atom",
            "POST",
            a,
            201,
            headers=admin,
            json={
                "name": "Sim monitor",
                "model_tier": "small",
                "max_runs_per_day": 10,
                "max_cost_per_day": "1.000000",
                "max_actions_per_day": 20,
            },
        )
        path = f"{a}/{atom['id']}"
        version = self.call(
            "propose version",
            "POST",
            f"{path}/versions",
            201,
            headers=admin,
            json={"instructions": "Check the daily totals."},
        )
        schedule = self.call(
            "add schedule",
            "POST",
            f"{path}/schedules",
            201,
            headers=admin,
            json={"interval_minutes": 5},
        )
        self.call(
            "activate",
            "POST",
            f"{path}/activate",
            200,
            headers=admin,
            json={"version_id": version["id"]},
        )
        self.call(
            "grant memory read",
            "PUT",
            f"{path}/grants",
            200,
            headers=admin,
            json={"resource_type": "memory", "level": "read"},
        )
        self.call(
            "grant summary",
            "PUT",
            f"{path}/grants",
            200,
            headers=admin,
            json={"resource_type": "project", "level": "summary"},
        )
        connection = self.call(
            "connect toolkit",
            "PUT",
            f"{path}/connections",
            200,
            headers=admin,
            json={
                "toolkit": "outlook",
                "composio_user_id": "workspace",
                "composio_account_ref": "account",
                "toolkit_version": "20261001_00",
                "permission_ceiling": "write",
                "status": "active",
                "allowed_tools": ["OUTLOOK_SEND"],
            },
        )
        self.call("list atoms", "GET", a, 200, headers=admin)
        self.call("get atom", "GET", path, 200, headers=admin)
        self.call("scheduler workspaces", "GET", "/atoms/scheduler-workspaces", 200)
        self.call("scheduler state", "GET", f"{a}/scheduler-state", 200)
        self.call(
            "scheduler refused for a human token", "GET", f"{a}/scheduler-state", 403, headers=admin
        )

        start = {
            "schedule_id": schedule["id"],
            "scheduled_for": datetime.now(UTC).isoformat(),
            "idempotency_key": f"sim-{uuid.uuid4().hex[:6]}",
        }
        run = self.call("scheduler starts a run", "POST", f"{path}/runs", 200, json=start)
        again = self.call("same slot returns the same run", "POST", f"{path}/runs", 200, json=start)
        self.check("run start is idempotent", run["id"] == again["id"])

        scopes = {
            "atoms:read",
            "atoms:run",
            "atoms:propose",
            "skills:read",
            "skills:write",
            "aggregate:read",
            "memory:read",
        }
        atom_h, _, _ = self.token(
            "run",
            scopes,
            mem=atom["member_id"],
            agent=atom["id"],
            run=run["id"],
            sub="atom",
            ttl=600,
        )
        self.call("atom token cannot administer", "GET", a, 403, headers=atom_h)
        self.call(
            "atom reads granted memory list", "GET", f"{self.base}/memories", 200, headers=atom_h
        )
        self.call(
            "atom run token fails on another workspace",
            "GET",
            f"/workspaces/{uuid.uuid4()}/memories",
            403,
            headers=atom_h,
        )
        self.mcp("atom_load", atom_h, "atom_load")
        self.mcp("atom_run_start (retry)", atom_h, "atom_run_start", start)
        self.mcp(
            "atom memory recall", atom_h, "memory_recall", {"query": "database", "agent": "sim"}
        )
        self.mcp(
            "atom proposes a version",
            atom_h,
            "atom_propose_version",
            {"instructions": "Candidate instructions."},
        )
        skill = self.mcp(
            "skill_write",
            atom_h,
            "skill_write",
            {
                "name": "Daily count",
                "description": "Count daily totals",
                "when_to_use": "For a daily report",
                "instructions": "Read the aggregate.",
                "scope": "workspace",
            },
        )
        self.mcp(
            "skill_attach", atom_h, "skill_attach", {"skill_id": skill["id"], "skill_version": 1}
        )
        found = self.mcp("skills_search", atom_h, "skills_search", {"query": "daily totals"})
        self.check(
            "skills_search finds the skill", skill["id"] in [s["id"] for s in found["skills"]]
        )
        self.mcp("aggregate_read", atom_h, "aggregate_read", {"resource_type": "memory"})

        body = {
            "kind": "atom_action",
            "title": "Send report",
            "assigned_to": self.members["owner"],
            "payload": {
                "tool_slug": "OUTLOOK_SEND",
                "arguments": {"subject": "Report"},
                "connection_id": connection["id"],
            },
        }
        approval = self.mcp("approval create", atom_h, "atom_approval_create", body)
        ap = f"{self.base}/approvals/{approval['id']}"
        self.call(
            "atom cannot decide its own approval",
            "PUT",
            f"{ap}/decision",
            403,
            headers=atom_h,
            json={"status": "approved"},
        )
        self.call(
            "human approves",
            "PUT",
            f"{ap}/decision",
            200,
            headers={**self.service, "X-Acting-Member-Id": self.members["owner"]},
            json={"status": "approved"},
        )
        ready = self.call("approvals ready", "GET", f"{self.base}/approvals/ready", 200)
        self.check("approval is ready", any(r["id"] == approval["id"] for r in ready or []))
        self.mcp(
            "atom finishes its first run",
            atom_h,
            "atom_run_finish",
            {"status": "succeeded", "cost": "0.01"},
        )
        execution = self.call(
            "backend bootstraps the execution run", "POST", f"{ap}/execution-run", 200, json={}
        )
        exec_h, _, _ = self.token(
            "run",
            {"atoms:run"},
            mem=atom["member_id"],
            agent=atom["id"],
            run=execution["id"],
            sub="atom",
        )
        claim = self.mcp(
            "claim approval", exec_h, "atom_approval_claim", {"approval_id": approval["id"]}
        )
        self.check("first claim may execute", claim and claim.get("execute") is True)
        again = self.call(
            "second claim must not execute", "POST", f"{ap}/claim", 200, headers=exec_h
        )
        self.check("second claim is refused execution", again and again.get("execute") is False)
        self.call(
            "report outcome",
            "PUT",
            f"{ap}/outcome",
            200,
            headers=exec_h,
            json={"status": "succeeded", "result": {"id": "external"}},
        )
        self.mcp("finish execution run", exec_h, "atom_run_finish", {"status": "succeeded"})
        self.call(
            "finished run token is refused for new work",
            "GET",
            f"{self.base}/memories",
            403,
            headers=atom_h,
        )
        self.call(
            "rollback unknown version is refused",
            "POST",
            f"{path}/rollback",
            (404, 422),
            headers=admin,
            json={"version_id": str(uuid.uuid4())},
        )
        self.call("delete atom", "DELETE", path, 204, headers=admin)

    def security(self):
        self.call("no credentials", "GET", f"{self.base}/members", 401, headers={})
        self.call(
            "wrong service key",
            "GET",
            f"{self.base}/members",
            401,
            headers={"X-Data-API-Key": "nope"},
        )
        old = jwt.encode(
            {"iss": "flwn-data-engine", "sub": "x", "ws": self.ws, "exp": int(time.time()) + 60},
            "x" * 40,
            algorithm="HS256",
        )
        self.call(
            "legacy HS256 token",
            "GET",
            f"{self.base}/members",
            401,
            headers={"Authorization": f"Bearer {old}"},
        )
        expired, _, _ = self.token("human", {"admin"}, mem=self.members["owner"], ttl=-30)
        self.call("expired token", "GET", f"{self.base}/members", 401, headers=expired)
        other = Ed25519PrivateKey.generate()
        forged = jwt.encode(
            {
                "iss": ISS,
                "aud": [AUD],
                "sub": "x",
                "ws": self.ws,
                "typ": "human",
                "ctx": "workspace",
                "mem": self.members["owner"],
                "scope": "admin",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "jti": uuid.uuid4().hex,
            },
            other,
            algorithm="EdDSA",
            headers={"kid": KID},
        )
        self.call(
            "token signed by another key",
            "GET",
            f"{self.base}/members",
            401,
            headers={"Authorization": f"Bearer {forged}"},
        )
        wrong_aud = jwt.encode(
            {
                "iss": ISS,
                "aud": ["other"],
                "sub": "x",
                "ws": self.ws,
                "typ": "human",
                "ctx": "workspace",
                "mem": self.members["owner"],
                "scope": "admin",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "jti": uuid.uuid4().hex,
            },
            self.key,
            algorithm="EdDSA",
            headers={"kid": KID},
        )
        self.call(
            "wrong audience",
            "GET",
            f"{self.base}/members",
            401,
            headers={"Authorization": f"Bearer {wrong_aud}"},
        )
        revoked, jti, exp = self.token("human", {"admin"}, mem=self.members["owner"])
        self.call(
            "token works before revocation", "GET", f"{self.base}/members", 200, headers=revoked
        )
        self.call(
            "human cannot revoke",
            "POST",
            "/auth/revocations",
            (401, 403),
            headers=revoked,
            json={"jti": jti, "expires_at": exp},
        )
        self.call(
            "backend revokes",
            "POST",
            "/auth/revocations",
            204,
            json={"jti": jti, "expires_at": exp},
        )
        self.call("revoked token is refused", "GET", f"{self.base}/members", 401, headers=revoked)
        self.call(
            "revoked token fails the session check", "GET", "/auth/session", 401, headers=revoked
        )
        suspended = self.call(
            "suspend the member",
            "PATCH",
            f"{self.base}/members/{self.members['member']}",
            200,
            json={"status": "suspended"},
        )
        self.call(
            "suspended member is refused",
            "GET",
            f"{self.base}/projects",
            403,
            headers=self.member_h,
        )
        _ = suspended

    def cleanup(self):
        for label, path in (("delete workspace", f"/workspaces/{self.ws}"),):
            self.call(label, "DELETE", path, (200, 204))
        for user in self.users:
            self.call("delete user", "DELETE", f"/users/{user['id']}", (200, 204))

    def run(self):
        steps = (self.backend_setup, self.humans, self.ai_engine, self.atoms, self.security)
        try:
            for step in steps:
                print(f"== {step.__name__}")
                try:
                    step()
                except Exception as exc:  # a failed step must not skip the cleanup
                    self.results.append(
                        (False, f"{step.__name__} crashed: {type(exc).__name__}: {exc}")
                    )
                    print(f"  CRASH {step.__name__}: {type(exc).__name__}: {exc}")
        finally:
            if self.ws:
                print("== cleanup")
                self.cleanup()
        failed = [text for ok, text in self.results if not ok]
        print(f"\n{len(self.results) - len(failed)} passed, {len(failed)} failed")
        return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "keygen":
        keygen(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "run":
        sys.exit(Sim(sys.argv[2], sys.argv[3]).run())
    else:
        sys.exit(__doc__)
