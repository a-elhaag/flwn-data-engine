"""Seed simulated workspaces with members, planning, chat, docs and atoms, for manual testing.

    python -m app.db.seed             # add the seed workspaces (skips ones that already exist)
    python -m app.db.seed --reset     # delete the seed workspaces and their users, then add them again
    python -m app.db.seed --url https://<container-app-host>   # seed through a deployed data engine

Everything goes through the API, so each row passes the same validation as a real request. With
--url the calls go to a deployed data engine (its service key is DATA_API_KEY); without it the app
runs in this process. Seed data is easy to find and remove: workspace slugs start with `seed-` and every human
uses an @seed.flwn.test address. Nothing else is touched. It needs DATABASE_URL and DATA_API_KEY
(from the environment or .env), like the app.

Not seeded: memories and files (they call the embedding model), meetings (they need file storage),
and approvals (they need a live atom run).
"""

import argparse
import sys
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import session as db
from app.db.base import PRIORITIES, WORK_STATUSES

SEED_DOMAIN = "seed.flwn.test"
SLUG_PREFIX = "seed-"

# (name, role) per person; the first is the owner. Two AI agents join each workspace.
PEOPLE = [
    ("Amira Hassan", "owner"),
    ("Noah Becker", "admin"),
    ("Lena Kovac", "member"),
    ("Omar Saleh", "member"),
    ("Priya Nair", "member"),
    ("Tomas Reyes", "viewer"),
]
AGENTS = [("Ghost Engineer", "ghost_engineer"), ("Scrum Master", "scrum_master")]

WORKSPACES = [
    {
        "slug": "seed-acme",
        "name": "Seed: Acme Robotics",
        "teams": [("ENG", "Engineering"), ("OPS", "Operations")],
        "projects": [
            ("ARM", "Robot arm firmware", "ENG", ["Calibrate joints", "Safety stop", "Telemetry"]),
            ("DOCK", "Charging dock", "OPS", ["Order parts", "Install pilot", "Field test"]),
        ],
        "atoms": [
            ("Daily standup digest", "active", 60),
            ("Release notes drafter", "draft", None),
            ("Inbox triage", "paused", 120),
        ],
    },
    {
        "slug": "seed-northwind",
        "name": "Seed: Northwind Studio",
        "teams": [("DSN", "Design"), ("MKT", "Marketing")],
        "projects": [
            ("BRAND", "Brand refresh", "DSN", ["Moodboard", "Logo options", "Style guide"]),
            ("LAUNCH", "Spring launch", "MKT", ["Landing page", "Email series", "Press kit"]),
        ],
        "atoms": [
            ("Campaign reporter", "active", 1440),
            ("Asset tagger", "draft", None),
        ],
    },
]


class Seeder:
    def __init__(self, url: str | None = None):
        headers = {"X-Data-API-Key": settings.DATA_API_KEY}
        if url:  # a deployed data engine; it may be scaled to zero, so allow a slow first call
            import httpx

            self.client = httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=180)
        else:
            from app.main import create_app

            self.client = TestClient(create_app(), headers=headers)
        self.client.__enter__()

    def close(self):
        self.client.__exit__(None, None, None)

    def call(self, method, url, *, as_member=None, workspace=None, expect=(200, 201, 204), **kw):
        # The service key acts as a member through this header (no member tokens are needed).
        headers = {"X-Acting-Member-Id": as_member} if as_member else {}
        response = self.client.request(method, url, headers=headers, **kw)
        if response.status_code not in expect:
            raise RuntimeError(f"{method} {url} -> {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else None

    # -- one workspace ----------------------------------------------------------------------

    def workspace(self, spec) -> dict:
        ws = self.call("POST", "/workspaces", json={"name": spec["name"], "slug": spec["slug"]})[
            "id"
        ]
        base = f"/workspaces/{ws}"
        people = {}
        for name, role in PEOPLE:
            email = f"{name.split()[0].lower()}.{spec['slug']}@{SEED_DOMAIN}"
            body = {"type": "HUMAN", "role": role, "name": name, "email": email}
            people[name] = self.call("POST", f"{base}/members", json=body)["id"]
        agents = {}
        for name, kind in AGENTS:
            body = {"type": "AI", "name": name, "agent_kind": kind}
            agents[name] = self.call("POST", f"{base}/members", json=body)["id"]
        owner = people[PEOPLE[0][0]]
        names = [n for n, _ in PEOPLE]

        def acting(method, path, member=owner, **kw):
            return self.call(method, f"{base}{path}", as_member=member, workspace=ws, **kw)

        teams = {}
        for index, (key, name) in enumerate(spec["teams"]):
            teams[key] = acting("POST", "/teams", json={"key": key, "name": name})["id"]
            for person in names[index::2] + [names[0]]:
                self.call(
                    "POST",
                    f"{base}/team-members",
                    expect=(200, 201, 409),  # service-only route
                    json={"team_id": teams[key], "member_id": people[person]},
                )
        counts = {
            "members": len(people) + len(agents),
            "teams": len(teams),
            "projects": 0,
            "work_items": 0,
            "tasks": 0,
            "sprints": 0,
            "channels": 0,
            "docs": 0,
            "atoms": 0,
        }
        task_for_comment = None
        for key, name, team_key, items in spec["projects"]:
            project = acting(
                "POST",
                "/projects",
                json={
                    "key": key,
                    "name": name,
                    "team_id": teams[team_key],
                    "lead_id": people[names[1]],
                    "status": "in_progress",
                },
            )["id"]
            counts["projects"] += 1
            today = date.today()
            sprint = acting(
                "POST",
                f"/projects/{project}/sprints",
                json={
                    "name": "Sprint 1",
                    "goal": f"First cut of {name}",
                    "status": "active",
                    "start_date": today.isoformat(),
                    "end_date": (today + timedelta(days=14)).isoformat(),
                },
            )["id"]
            counts["sprints"] += 1
            for number, title in enumerate(items):
                item = acting(
                    "POST",
                    f"/projects/{project}/work-items",
                    json={
                        "title": title,
                        "type": ("FEATURE", "STORY", "BUG")[number % 3],
                        "priority": PRIORITIES[1 + number % 4],
                        "status": WORK_STATUSES[number % 4],
                    },
                )["id"]
                counts["work_items"] += 1
                for step, label in enumerate(("Plan", "Build", "Review")):
                    task = acting(
                        "POST",
                        f"/projects/{project}/tasks",
                        json={
                            "work_item_id": item,
                            "title": f"{label}: {title}",
                            "sprint_id": sprint,
                            "assignee_id": people[names[2 + (number + step) % 3]],
                            "priority": PRIORITIES[1 + step],
                            "status": WORK_STATUSES[step % 3],
                            "estimate": 1 + step * 2,
                            "due_date": (today + timedelta(days=3 + step)).isoformat(),
                        },
                    )["id"]
                    counts["tasks"] += 1
                    task_for_comment = task_for_comment or task
        if task_for_comment:
            acting(
                "POST",
                "/comments",
                json={
                    "target_type": "task",
                    "target_id": task_for_comment,
                    "body": "Blocked on the vendor sample. Can we ship without it?",
                },
            )
        for name, kind, team_key in (
            ("general", "group", None),
            ("engineering", "team", spec["teams"][0][0]),
        ):
            channel = acting(
                "POST",
                "/channels",
                json={
                    "type": kind,
                    "name": name,
                    "topic": f"{spec['name']} {name}",
                    **({"team_id": teams[team_key]} if team_key else {}),
                    "member_ids": list(people.values()),
                },
            )["id"]
            counts["channels"] += 1
            for index, line in enumerate(
                ("Morning all, standup in 10.", "Notes are in the doc.", "Thanks, merged.")
            ):
                acting(
                    "POST",
                    f"/channels/{channel}/messages",
                    member=people[names[index]],
                    json={"body": line},
                )
        for title in ("Team handbook", "Release checklist"):
            acting("POST", "/docs", json={"title": title})
            counts["docs"] += 1
        for name, status, minutes in spec["atoms"]:
            self.atom(ws, owner, name, status, minutes)
            counts["atoms"] += 1
        return {"workspace_id": ws, "owner": owner, **counts}

    def atom(self, ws, owner, name, status, minutes):
        def acting(method, path, **kw):
            return self.call(
                method, f"/workspaces/{ws}/atoms{path}", as_member=owner, workspace=ws, **kw
            )

        atom = acting(
            "POST",
            "",
            json={
                "name": name,
                "model_tier": "small",
                "max_runs_per_day": 24,
                "max_cost_per_day": "2.000000",
                "max_actions_per_day": 100,
            },
        )["id"]
        version = acting(
            "POST",
            f"/{atom}/versions",
            json={
                "instructions": f"{name}: read the workspace summary and report what changed.",
                "policy": {"max_cost": "0.50", "max_actions": 20},
            },
        )["id"]
        if status == "draft":
            return
        acting("PUT", f"/{atom}/grants", json={"resource_type": "memory", "level": "read"})
        acting("POST", f"/{atom}/schedules", json={"interval_minutes": minutes})
        acting("POST", f"/{atom}/activate", json={"version_id": version})
        if status == "paused":
            # Builds before the status fix reject this; the atom then stays active.
            result = acting("PATCH", f"/{atom}", json={"status": "paused"}, expect=(200, 422))
            if result.get("status") != "paused":
                print(f"note: {name} stays active; this data engine cannot pause an atom yet")


def reset():
    """Hard-delete the seed workspaces and users. Matches only `seed-` slugs and @seed.flwn.test emails."""
    with db.workspace_session(db.engine(), "", service=True) as session:
        ids = [
            str(r[0])
            for r in session.execute(
                text("select id from workspaces where slug like :p"), {"p": SLUG_PREFIX + "%"}
            )
        ]
        for table in ("atoms",):  # atoms reference their owner member, so go before the cascade
            session.execute(
                text(f"delete from {table} where workspace_id = any(cast(:ids as uuid[]))"),
                {"ids": ids},
            )
        session.execute(
            text("delete from workspaces where id = any(cast(:ids as uuid[]))"), {"ids": ids}
        )
        session.execute(text("delete from users where email like :d"), {"d": f"%@{SEED_DOMAIN}"})
    return len(ids)


def existing_slugs() -> set[str]:
    with db.workspace_session(db.engine(), "", service=True) as session:
        rows = session.execute(
            text("select slug from workspaces where slug like :p"), {"p": SLUG_PREFIX + "%"}
        )
        return {r[0] for r in rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reset", action="store_true", help="delete the seed workspaces first")
    parser.add_argument(
        "--url", help="seed through this deployed data engine instead of in-process"
    )
    args = parser.parse_args(argv)
    if args.reset:
        print(f"removed {reset()} seed workspace(s)")
    present = existing_slugs()
    seeder = Seeder(args.url)
    try:
        for spec in WORKSPACES:
            if spec["slug"] in present:
                print(f"skip {spec['slug']}: already seeded (use --reset to rebuild)")
                continue
            print(spec["slug"], seeder.workspace(spec))
    finally:
        seeder.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
