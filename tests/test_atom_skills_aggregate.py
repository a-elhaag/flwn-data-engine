"""Focused skill and content-free aggregate contracts against disposable PostgreSQL."""

import json
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import db_support
import env  # noqa: F401
from sqlalchemy import delete, select, text, update
from test_atoms_schema import make_atom, make_connection, make_skill, make_version
from test_schema import T, add, make_workspace

from app.atoms.aggregate import AggregateService
from app.atoms.skills import SkillService
from app.db.install import grant_app_role

VECTOR = [1.0] + [0.0] * 1535


def setUpModule():
    global ENGINE
    ENGINE = db_support.engine()


class SkillsAggregateTest(unittest.TestCase):
    def setUp(self):
        self.conn = ENGINE.connect()
        self.tx = self.conn.begin()
        self.conn.begin_nested()
        self.ws = make_workspace(self.conn, "skill-aggregate")
        self.conn.execute(
            update(T("members")).where(T("members").c.id == self.ws["human"]).values(role="admin")
        )
        self.atom = make_atom(self.conn, self.ws)
        version = make_version(self.conn, self.atom)
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(active_version_id=version, status="active")
        )
        self.run = add(
            self.conn,
            "agent_runs",
            workspace_id=self.ws["workspace_id"],
            agent_id=self.atom["member_id"],
            atom_id=self.atom["id"],
            atom_version_id=version,
            trigger="schedule",
            status="running",
        )
        self.service = SkillService(self.ws["workspace_id"], self.ws["human"], bind=self.conn)
        self.aggregate = AggregateService(
            self.ws["workspace_id"],
            self.atom["member_id"],
            atom_id=self.atom["id"],
            run_id=self.run,
            bind=self.conn,
        )
        self.embed = patch("app.atoms.skills.vectorizer.embed_one", return_value=VECTOR).start()
        self.chat = patch(
            "app.clients.inference.chat", side_effect=AssertionError("no model execution")
        ).start()

    def tearDown(self):
        patch.stopall()
        self.tx.rollback()
        self.conn.close()

    def task(self, **values):
        defaults = dict(
            workspace_id=self.ws["workspace_id"],
            project_id=self.ws["project"],
            work_item_id=self.ws["item"],
            title="Private title must never escape",
            description="Confidential description",
            status="todo",
        )
        defaults.update(values)
        return add(self.conn, "tasks", **defaults)

    def grant(self, resource_type="project", resource_id=None, **values):
        defaults = dict(
            workspace_id=self.ws["workspace_id"],
            atom_id=self.atom["id"],
            resource_type=resource_type,
            resource_id=resource_id,
            level="summary",
            constraints={},
        )
        defaults.update(values)
        return add(self.conn, "atom_grants", **defaults)

    def test_write_forces_owner_and_embeds_only_description_and_trigger(self):
        result = self.service.write(
            "daily",
            "Daily updates",
            "At end of day",
            "SECRET INSTRUCTIONS",
            scope="personal",
            owner_member_id=self.ws["agent"],
        )
        row = self.conn.execute(
            select(T("skills")).where(T("skills").c.id == uuid.UUID(result["id"]))
        ).one()
        self.assertEqual(row.owner_member_id, self.ws["human"])
        self.embed.assert_called_once_with("Daily updates\nAt end of day")
        self.assertTrue(result["community"])
        self.assertEqual(result["trust_tier"], "community")
        self.chat.assert_not_called()

    def test_catalog_and_nonadmin_workspace_writes_are_denied(self):
        with self.assertRaises(PermissionError):
            self.service.write("daily", "Description", "When", "Instructions", scope="catalog")
        self.conn.execute(
            update(T("members")).where(T("members").c.id == self.ws["human"]).values(role="member")
        )
        with self.assertRaises(PermissionError):
            self.service.write("daily", "Description", "When", "Instructions")
        self.service.write("private", "Description", "When", "Instructions", scope="personal")
        self.assertEqual(self.conn.scalar(select(T("skills").c.owner_member_id)), self.ws["human"])

    def test_workspace_atom_can_write_search_and_attach_community_skill(self):
        caller = SkillService(
            self.ws["workspace_id"],
            self.atom["member_id"],
            atom_id=self.atom["id"],
            run_id=self.run,
            bind=self.conn,
        )
        grant_app_role(self.conn, "atom_skill_test_app")
        self.conn.execute(text("set local role atom_skill_test_app"))
        result = caller.write(
            "learned",
            "Learned procedure",
            "During daily work",
            "Private steps",
            owner_member_id=self.ws["human"],
        )
        self.assertEqual(result["scope"], "workspace")
        self.assertEqual(result["trust_tier"], "community")
        self.assertTrue(result["community"])
        self.assertEqual([row["id"] for row in caller.search("Learned procedure")], [result["id"]])
        link = caller.attach(result["id"], skill_version=1)
        self.assertTrue(link["community"])
        self.assertEqual(caller.attached()[0]["instructions"], "Private steps")
        self.assertIsNone(self.conn.scalar(select(T("skills").c.owner_member_id)))
        with self.assertRaises(PermissionError):
            caller.promote(result["id"])
        with self.assertRaises(PermissionError):
            caller.write("catalog-learned", "Description", "When", "Steps", scope="catalog")
        restricted = caller.write(
            "restricted",
            "Description",
            "When",
            "Steps",
            tools_required=[{"ref": "composio:outlook/SEND_MAIL", "minimum_permission": "write"}],
        )
        with self.assertRaises(PermissionError):
            caller.attach(restricted["id"], skill_version=1)

    def test_personal_atom_cannot_publish_or_choose_another_skill_owner(self):
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(kind="personal", owner_member_id=self.ws["human"])
        )
        caller = SkillService(
            self.ws["workspace_id"],
            self.atom["member_id"],
            atom_id=self.atom["id"],
            run_id=self.run,
            bind=self.conn,
        )
        with self.assertRaises(PermissionError):
            caller.write("public-learned", "Description", "When", "Steps")
        result = caller.write(
            "private-learned",
            "Description",
            "When",
            "Steps",
            scope="personal",
            owner_member_id=self.ws["agent"],
        )
        self.assertEqual(
            self.conn.scalar(select(T("skills").c.owner_member_id)), self.atom["member_id"]
        )
        self.assertEqual(result["scope"], "personal")
        caller.attach(result["id"], skill_version=1)
        self.assertEqual(caller.attached()[0]["id"], result["id"])
        self.assertEqual(self.service.search("Description"), [])
        with self.assertRaises(PermissionError):
            caller.promote(result["id"])

    def test_hybrid_search_prefers_personal_then_workspace_then_catalog(self):
        personal = make_skill(
            self.conn,
            self.ws,
            name="personal",
            scope="personal",
            owner_member_id=self.ws["human"],
            embedding=VECTOR,
        )
        local = make_skill(self.conn, self.ws, name="local", embedding=VECTOR)
        catalog = make_skill(self.conn, embedding=VECTOR)
        other = make_workspace(self.conn, "hidden-skills")
        make_skill(self.conn, other, embedding=VECTOR)
        make_skill(
            self.conn,
            self.ws,
            name="hidden",
            scope="personal",
            owner_member_id=self.ws["agent"],
            embedding=VECTOR,
        )
        result = self.service.search("Summarize updates", limit=10)
        self.assertEqual([row["id"] for row in result], list(map(str, (personal, local, catalog))))
        self.assertTrue(all(row["community"] for row in result))
        self.assertTrue(all("instructions" not in row for row in result))
        self.chat.assert_not_called()

    def test_keyword_only_skill_without_vector_is_discovered(self):
        row = make_skill(self.conn, self.ws, embedding=None, description="NEEDLE-123")
        result = self.service.search("NEEDLE-123")
        self.assertEqual([hit["id"] for hit in result], [str(row)])

    def test_attach_checks_exact_version_permissions_allowlist_and_status(self):
        skill = make_skill(
            self.conn,
            self.ws,
            tools_required=[{"ref": "composio:outlook/SEND_MAIL", "minimum_permission": "write"}],
        )
        for version in (0, 2):
            with self.assertRaises((ValueError, LookupError)):
                self.service.attach(skill, skill_version=version, atom_id=self.atom["id"])
        connection = make_connection(
            self.conn,
            self.atom,
            self.ws["human"],
            status="active",
            composio_account_ref="account",
            allowed_tools=["SEND_MAIL"],
        )
        with self.assertRaises(PermissionError):
            self.service.attach(skill, skill_version=1, atom_id=self.atom["id"])
        self.conn.execute(
            update(T("atom_connections"))
            .where(T("atom_connections").c.id == connection)
            .values(permission_ceiling="write", allowed_tools=[])
        )
        with self.assertRaises(PermissionError):
            self.service.attach(skill, skill_version=1, atom_id=self.atom["id"])
        self.conn.execute(
            update(T("atom_connections"))
            .where(T("atom_connections").c.id == connection)
            .values(allowed_tools=["SEND_MAIL"], status="expired")
        )
        with self.assertRaises(PermissionError):
            self.service.attach(skill, skill_version=1, atom_id=self.atom["id"])
        self.conn.execute(
            update(T("atom_connections"))
            .where(T("atom_connections").c.id == connection)
            .values(status="active")
        )
        attached = self.service.attach(skill, skill_version=1, atom_id=self.atom["id"])
        again = self.service.attach(skill, skill_version=1, atom_id=self.atom["id"])
        self.assertEqual(attached["id"], again["id"])
        self.assertEqual(attached["skill_version"], 1)
        self.assertTrue(attached["community"])
        self.assertEqual(len(self.service.list_attached(self.atom["id"])), 1)
        self.conn.execute(
            update(T("atom_connections"))
            .where(T("atom_connections").c.id == connection)
            .values(status="revoked")
        )
        with self.assertRaises(PermissionError):
            self.service.list_attached(self.atom["id"])

    def test_attached_loads_exact_enabled_visible_local_and_catalog_content(self):
        local = make_skill(self.conn, self.ws, name="pinned", instructions="Pinned v1")
        make_skill(self.conn, self.ws, name="pinned", version=2, instructions="Unattached v2")
        catalog = make_skill(self.conn, name="catalog-pinned", instructions="Catalog instructions")
        hidden = make_skill(
            self.conn,
            self.ws,
            name="hidden-personal",
            scope="personal",
            owner_member_id=self.ws["agent"],
        )
        disabled = make_skill(self.conn, self.ws, name="disabled")
        deleted = make_skill(self.conn, name="deleted-catalog", deleted_at=datetime.now(UTC))
        for skill_id, is_catalog, enabled in (
            (local, False, True),
            (catalog, True, True),
            (hidden, False, True),
            (disabled, False, False),
            (deleted, True, True),
        ):
            add(
                self.conn,
                "atom_skills",
                workspace_id=self.ws["workspace_id"],
                atom_id=self.atom["id"],
                skill_id=None if is_catalog else skill_id,
                catalog_skill_id=skill_id if is_catalog else None,
                skill_version=1,
                added_by="human",
                added_by_member_id=self.ws["human"],
                enabled=enabled,
            )
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(status="draft", active_version_id=None)
        )
        result = self.service.attached(self.atom["id"])
        by_id = {row["id"]: row for row in result}
        self.assertEqual(set(by_id), {str(local), str(catalog)})
        self.assertEqual(by_id[str(local)]["instructions"], "Pinned v1")
        self.assertEqual(by_id[str(catalog)]["instructions"], "Catalog instructions")
        self.assertTrue(all(row["version"] == 1 for row in result))
        self.assertTrue(all(not {"embedding", "tsv"}.intersection(row) for row in result))
        self.conn.execute(
            update(T("skills"))
            .where(T("skills").c.id == local)
            .values(deleted_at=datetime.now(UTC))
        )
        self.assertEqual(
            [row["id"] for row in self.service.attached(self.atom["id"])], [str(catalog)]
        )

    def test_attached_rechecks_connection_tool_allowlist_and_ceiling(self):
        skill = make_skill(
            self.conn,
            self.ws,
            tools_required=[{"ref": "composio:outlook/SEND_MAIL", "minimum_permission": "write"}],
        )
        connection = make_connection(
            self.conn,
            self.atom,
            self.ws["human"],
            status="active",
            composio_account_ref="account",
            permission_ceiling="write",
            allowed_tools=["SEND_MAIL"],
        )
        self.service.attach(skill, skill_version=1, atom_id=self.atom["id"])
        self.assertEqual(len(self.service.attached(self.atom["id"])), 1)
        for fields in (
            {"allowed_tools": []},
            {"permission_ceiling": "read"},
            {"status": "revoked"},
        ):
            self.conn.execute(
                update(T("atom_connections"))
                .where(T("atom_connections").c.id == connection)
                .values(status="active", permission_ceiling="write", allowed_tools=["SEND_MAIL"])
            )
            self.conn.execute(
                update(T("atom_connections"))
                .where(T("atom_connections").c.id == connection)
                .values(**fields)
            )
            with self.subTest(fields=fields), self.assertRaises(PermissionError):
                self.service.attached(self.atom["id"])

    def test_attachment_rejects_foreign_workspace_and_personal_skill_on_workspace_atom(self):
        other = make_workspace(self.conn, "foreign-skill")
        foreign = make_skill(self.conn, other)
        personal = make_skill(
            self.conn, self.ws, scope="personal", owner_member_id=self.ws["human"]
        )
        with self.assertRaises(LookupError):
            self.service.attach(foreign, skill_version=1, atom_id=self.atom["id"])
        with self.assertRaises(PermissionError):
            self.service.attach(personal, skill_version=1, atom_id=self.atom["id"])

    def test_aggregate_returns_only_safe_counts_groups_and_trends(self):
        self.task(status="todo")
        self.task(status="done")
        self.grant(resource_id=self.ws["project"])
        result = self.aggregate.read(
            "project", resource_id=self.ws["project"], group_by="status", trend="day"
        )
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            result["groups"], [{"value": "done", "count": 1}, {"value": "todo", "count": 1}]
        )
        self.assertEqual(sum(row["count"] for row in result["trend"]), 2)
        serialized = json.dumps(result)
        for forbidden in ("Private", "Confidential", "title", "body", str(self.ws["project"])):
            self.assertNotIn(forbidden, serialized)
        self.chat.assert_not_called()

    def test_attached_non_superuser_shared_connection_validates_before_elevation(self):
        from app.atoms.access import AtomContext, bind_context

        skill = make_skill(self.conn, self.ws, instructions="Pinned instructions")
        self.service.attach(skill, skill_version=1, atom_id=self.atom["id"])
        caller = SkillService(
            self.ws["workspace_id"],
            self.atom["member_id"],
            atom_id=self.atom["id"],
            run_id=self.run,
            bind=self.conn,
        )
        context = AtomContext(
            str(self.ws["workspace_id"]),
            str(self.atom["id"]),
            str(self.run),
            str(self.atom["member_id"]),
        )
        grant_app_role(self.conn, "atom_skill_test_app")
        self.conn.execute(text("set local role atom_skill_test_app"))
        for bound in (None, context):
            with self.subTest(bound=bound), bind_context(bound):
                loaded = caller.attached()
                self.assertEqual([row["id"] for row in loaded], [str(skill)])
                self.assertEqual(loaded[0]["instructions"], "Pinned instructions")
        self.conn.execute(text("reset role"))
        self.conn.execute(
            update(T("agent_runs"))
            .where(T("agent_runs").c.id == self.run)
            .values(status="succeeded")
        )
        self.conn.execute(text("set local role atom_skill_test_app"))
        with self.assertRaises(PermissionError):
            caller.attached()

    def test_summary_aggregate_operates_under_non_superuser_rls(self):
        self.task()
        self.grant(resource_id=self.ws["project"])
        grant_app_role(self.conn, "atom_skill_test_app")
        self.conn.execute(text("set local role atom_skill_test_app"))
        self.assertEqual(
            self.aggregate.read("project", group_by="status"),
            {"count": 1, "groups": [{"value": "todo", "count": 1}]},
        )

    def test_aggregate_grant_levels_and_revocation_are_live(self):
        self.task()
        grant = self.grant()
        for level in ("summary", "read", "write"):
            self.conn.execute(
                update(T("atom_grants")).where(T("atom_grants").c.id == grant).values(level=level)
            )
            self.assertEqual(self.aggregate.read("project"), {"count": 1})
        self.conn.execute(delete(T("atom_grants")).where(T("atom_grants").c.id == grant))
        with self.assertRaises(PermissionError):
            self.aggregate.read("project")

    def test_aggregate_denies_unknown_constraints_and_arbitrary_fields(self):
        grant = self.grant(constraints={"unknown": True})
        with self.assertRaises(PermissionError):
            self.aggregate.read("project")
        self.conn.execute(
            update(T("atom_grants")).where(T("atom_grants").c.id == grant).values(constraints={})
        )
        for field in (
            "title",
            "id",
            "description",
            "created_by",
            "properties",
            "status; DROP TABLE tasks",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.aggregate.read("project", group_by=field)
        with self.assertRaises(ValueError):
            self.aggregate.read("project", trend="second")

    def test_aggregate_since_days_applies_to_tasks_not_parent_project(self):
        self.task(created_at=datetime.now(UTC) - timedelta(days=10))
        self.task()
        self.grant(resource_id=self.ws["project"], constraints={"since_days": 2})
        self.assertEqual(self.aggregate.read("project"), {"count": 1})

    def test_aggregate_label_constraint_uses_structured_task_labels(self):
        selected = self.task()
        self.task()
        label = add(self.conn, "labels", workspace_id=self.ws["workspace_id"], name="release")
        self.conn.execute(
            T("task_labels")
            .insert()
            .values(workspace_id=self.ws["workspace_id"], task_id=selected, label_id=label)
        )
        self.grant(resource_id=self.ws["project"], constraints={"labels": ["release"]})
        self.assertEqual(self.aggregate.read("project"), {"count": 1})

    def test_connection_aggregate_cannot_count_ungranted_connections(self):
        permitted = make_connection(self.conn, self.atom, self.ws["human"])
        make_connection(self.conn, self.atom, self.ws["human"], toolkit="slack")
        self.grant("connection", permitted)
        self.assertEqual(self.aggregate.read("connection"), {"count": 1})

    def test_personal_aggregate_intersects_owner_private_team_membership(self):
        self.task()
        self.grant()
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(kind="personal", owner_member_id=self.ws["human"])
        )
        self.conn.execute(
            update(T("teams"))
            .where(T("teams").c.id == self.ws["team"])
            .values(visibility="private")
        )
        self.assertEqual(self.aggregate.read("project"), {"count": 0})
        self.conn.execute(
            T("team_members")
            .insert()
            .values(
                workspace_id=self.ws["workspace_id"],
                team_id=self.ws["team"],
                member_id=self.ws["human"],
            )
        )
        self.assertEqual(self.aggregate.read("project"), {"count": 1})

    def test_promote_delete_and_attachment_overrides_preserve_history(self):
        skill = self.service.write(
            "private", "Description", "When", "Instructions", scope="personal"
        )
        promoted = self.service.promote(skill["id"])
        self.assertEqual(promoted["scope"], "workspace")
        attached = self.service.attach(skill["id"], skill_version=1, atom_id=self.atom["id"])
        self.service.set_attachment(attached["id"], enabled=False, atom_id=self.atom["id"])
        self.assertEqual(self.service.list_attached(self.atom["id"]), [])
        atom_service = SkillService(
            self.ws["workspace_id"],
            self.atom["member_id"],
            atom_id=self.atom["id"],
            run_id=self.run,
            bind=self.conn,
        )
        with self.assertRaises(PermissionError):
            atom_service.attach(skill["id"], skill_version=1)
        self.service.set_attachment(attached["id"], enabled=True, atom_id=self.atom["id"])
        self.service.delete(skill["id"])
        self.assertEqual(self.service.list_attached(self.atom["id"]), [])
        self.assertEqual(self.conn.scalar(select(T("atom_skills").c.id)), uuid.UUID(attached["id"]))
        with self.assertRaises(LookupError):
            self.service.set_attachment(attached["id"], enabled=True, atom_id=self.atom["id"])

    def test_personal_atom_can_load_owners_personal_skill(self):
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(kind="personal", owner_member_id=self.ws["human"])
        )
        skill = self.service.write(
            "owner-private", "Description", "When", "Instructions", scope="personal"
        )
        self.service.attach(skill["id"], skill_version=1, atom_id=self.atom["id"])
        atom_service = SkillService(
            self.ws["workspace_id"],
            self.atom["member_id"],
            atom_id=self.atom["id"],
            run_id=self.run,
            bind=self.conn,
        )
        self.assertEqual([row["id"] for row in atom_service.list_attached()], [skill["id"]])

    def test_bad_actor_or_finished_run_cannot_aggregate(self):
        self.grant()
        wrong = AggregateService(
            self.ws["workspace_id"],
            self.ws["human"],
            atom_id=self.atom["id"],
            run_id=self.run,
            bind=self.conn,
        )
        with self.assertRaises(PermissionError):
            wrong.read("project")
        self.conn.execute(
            update(T("agent_runs"))
            .where(T("agent_runs").c.id == self.run)
            .values(status="succeeded")
        )
        with self.assertRaises(PermissionError):
            self.aggregate.read("project")
