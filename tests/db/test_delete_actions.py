"""Hard delete: every delete removes the row, and each foreign key does what the schema decided.

The decisions (issue #26): a workspace cascades to everything in it; a member is set null on the
work it was assigned or led; a team is set null on its projects; a user cannot be removed while a
membership remains; an atom is its member's identity, so removing the identity removes the atom;
a personal atom blocks its owner's removal; and no table keeps a deleted_at column.
"""

import unittest

import db_support
from factories import T, add, make_atom, make_version, make_workspace

try:
    from sqlalchemy import delete, select, text
    from sqlalchemy.exc import IntegrityError

    IMPORT_ERROR = None
except ImportError as exc:  # sqlalchemy not installed
    IMPORT_ERROR = exc


def setUpModule():
    if IMPORT_ERROR:
        raise unittest.SkipTest(f"delete tests need sqlalchemy: {IMPORT_ERROR}")
    global ENGINE
    ENGINE = db_support.engine()


class DeleteActionTest(unittest.TestCase):
    def setUp(self):
        self.conn = ENGINE.connect()
        self.tx = self.conn.begin()

    def tearDown(self):
        if self.tx.is_active:
            self.tx.rollback()
        self.conn.close()

    def count(self, table, **where):
        query = text(
            f"select count(*) from {table} where "
            + " and ".join(f"{column} = :{column}" for column in where)
        )
        return self.conn.execute(query, where).scalar()

    def refused(self, statement):
        """Run statement in a savepoint; a database rule must refuse it."""
        with self.assertRaises(IntegrityError):
            with self.conn.begin_nested():
                statement()

    def test_no_table_keeps_a_deleted_at_column(self):
        columns = (
            self.conn.execute(
                text(
                    "select table_name from information_schema.columns "
                    "where table_schema = 'public' and column_name = 'deleted_at'"
                )
            )
            .scalars()
            .all()
        )
        self.assertEqual(columns, [])

    def test_workspace_delete_removes_everything_inside_it(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        self.conn.execute(delete(T("workspaces")).where(T("workspaces").c.id == w))
        for table in ("members", "teams", "projects", "work_items", "backlogs"):
            with self.subTest(table=table):
                self.assertEqual(self.count(table, workspace_id=w), 0)

    def test_member_delete_sets_null_on_assignee_and_lead(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        task = add(
            self.conn,
            "tasks",
            workspace_id=w,
            project_id=a["project"],
            work_item_id=a["item"],
            title="Ship",
            assignee_id=a["agent"],
        )
        self.conn.execute(delete(T("members")).where(T("members").c.id == a["agent"]))
        self.assertIsNone(
            self.conn.scalar(select(T("tasks").c.assignee_id).where(T("tasks").c.id == task))
        )
        self.assertIsNotNone(
            self.conn.scalar(select(T("tasks").c.id).where(T("tasks").c.id == task))
        )

        self.conn.execute(
            text("update projects set lead_id = :m where id = :p"),
            {"m": a["human"], "p": a["project"]},
        )
        self.conn.execute(delete(T("members")).where(T("members").c.id == a["human"]))
        self.assertIsNone(
            self.conn.scalar(
                select(T("projects").c.lead_id).where(T("projects").c.id == a["project"])
            )
        )

    def test_team_delete_sets_null_on_its_projects(self):
        a = make_workspace(self.conn, "alpha")
        self.conn.execute(delete(T("teams")).where(T("teams").c.id == a["team"]))
        self.assertIsNone(
            self.conn.scalar(
                select(T("projects").c.team_id).where(T("projects").c.id == a["project"])
            )
        )

    def test_a_user_with_a_membership_cannot_be_deleted(self):
        a = make_workspace(self.conn, "alpha")
        user = self.conn.scalar(
            select(T("members").c.user_id).where(T("members").c.id == a["human"])
        )
        self.refused(lambda: self.conn.execute(delete(T("users")).where(T("users").c.id == user)))
        self.conn.execute(delete(T("members")).where(T("members").c.id == a["human"]))
        self.conn.execute(delete(T("users")).where(T("users").c.id == user))
        self.assertEqual(self.count("users", id=user), 0)

    def test_project_delete_takes_its_work_items_and_tasks(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        add(
            self.conn,
            "tasks",
            workspace_id=w,
            project_id=a["project"],
            work_item_id=a["item"],
            title="Ship",
        )
        self.conn.execute(delete(T("projects")).where(T("projects").c.id == a["project"]))
        self.assertEqual(self.count("work_items", id=a["item"]), 0)
        self.assertEqual(self.count("tasks", project_id=a["project"]), 0)

    def test_deleting_a_parent_message_unthreads_its_replies(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        channel = add(
            self.conn, "channels", workspace_id=w, team_id=a["team"], type="team", name="eng"
        )
        parent = add(
            self.conn,
            "messages",
            workspace_id=w,
            channel_id=channel,
            author_id=a["human"],
            body="a",
        )
        reply = add(
            self.conn,
            "messages",
            workspace_id=w,
            channel_id=channel,
            author_id=a["human"],
            body="b",
            parent_message_id=parent,
        )
        self.conn.execute(delete(T("messages")).where(T("messages").c.id == parent))
        self.assertIsNone(
            self.conn.scalar(
                select(T("messages").c.parent_message_id).where(T("messages").c.id == reply)
            )
        )

    def test_deleting_an_atom_removes_its_versions_then_its_identity(self):
        a = make_workspace(self.conn, "alpha")
        atom = make_atom(self.conn, a)
        make_version(self.conn, atom)
        # the identity cannot go first: its versions still name it as their creator
        self.refused(
            lambda: self.conn.execute(
                delete(T("members")).where(T("members").c.id == atom["member_id"])
            )
        )
        self.conn.execute(delete(T("atoms")).where(T("atoms").c.id == atom["id"]))
        self.assertEqual(self.count("atom_versions", atom_id=atom["id"]), 0)
        self.conn.execute(delete(T("members")).where(T("members").c.id == atom["member_id"]))
        self.assertEqual(self.count("members", id=atom["member_id"]), 0)

    def test_a_personal_atom_blocks_its_owners_removal(self):
        a = make_workspace(self.conn, "alpha")
        make_atom(self.conn, a, kind="personal", owner_member_id=a["human"])
        self.refused(
            lambda: self.conn.execute(delete(T("members")).where(T("members").c.id == a["human"]))
        )

    def test_events_outlive_the_member_they_name(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        add(
            self.conn,
            "events",
            workspace_id=w,
            actor_id=a["agent"],
            entity_type="task",
            entity_id=a["item"],
            action="created",
        )
        self.conn.execute(delete(T("members")).where(T("members").c.id == a["agent"]))
        rows = self.conn.execute(
            select(T("events").c.actor_id).where(T("events").c.workspace_id == w)
        ).all()
        self.assertEqual([row.actor_id for row in rows], [None])
