"""Schema tests: install the schema into a fresh Postgres and exercise the constraints.

Needs a Postgres with pgvector. Set TEST_DATABASE_URL to an admin URL (the tests create and drop
their own database), or `pip install pgserver` to get a throwaway local one. Skipped otherwise.
"""

import unittest
import uuid

import db_support

try:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import insert, text
    from sqlalchemy.exc import DBAPIError, IntegrityError

    import app.db.models  # noqa: F401
    from app.db.base import Base
    from app.db.session import workspace_session

    IMPORT_ERROR = None
except ImportError as exc:  # sqlalchemy / alembic / pgvector not installed
    IMPORT_ERROR = exc

T = lambda name: Base.metadata.tables[name]  # noqa: E731


def setUpModule():
    if IMPORT_ERROR:
        raise unittest.SkipTest(f"schema tests need sqlalchemy, alembic, pgvector: {IMPORT_ERROR}")
    global ENGINE
    ENGINE = db_support.engine()


def add(conn, table: str, **values) -> uuid.UUID:
    return conn.execute(insert(T(table)).values(**values).returning(T(table).c.id)).scalar_one()


def make_workspace(conn, slug: str) -> dict:
    """A workspace with a human, an agent, a team, and a project with one backlog item."""
    ws = {"workspace_id": add(conn, "workspaces", name=slug, slug=slug)}
    user = add(conn, "users", email=f"{slug}@example.com", name=slug)
    ws["human"] = add(conn, "members", workspace_id=ws["workspace_id"], user_id=user, type="HUMAN")
    ws["agent"] = add(
        conn,
        "members",
        workspace_id=ws["workspace_id"],
        type="AI",
        name="Ghost",
        agent_kind="ghost_engineer",
    )
    w = ws["workspace_id"]
    ws["team"] = add(conn, "teams", workspace_id=w, key="ENG", name="Engineering")
    ws["project"] = add(
        conn,
        "projects",
        workspace_id=w,
        team_id=ws["team"],
        key="FLWN",
        name="Flwn",
        created_by=ws["human"],
    )
    ws["backlog"] = add(conn, "backlogs", workspace_id=w, project_id=ws["project"], name="Main")
    ws["item"] = add(
        conn,
        "work_items",
        workspace_id=w,
        project_id=ws["project"],
        backlog_id=ws["backlog"],
        title="Login",
        type="STORY",
    )
    return ws


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.conn = ENGINE.connect()
        self.tx = self.conn.begin()

    def tearDown(self):
        if self.tx.is_active:
            self.tx.rollback()
        self.conn.close()

    def raises_integrity(self, statement):
        """Run statement in a savepoint; it must violate a constraint."""
        with self.assertRaises((IntegrityError, DBAPIError)):
            with self.conn.begin_nested():
                statement()

    # -- migration ---------------------------------------------------------

    def test_models_match_database(self):
        diff = compare_metadata(MigrationContext.configure(self.conn), Base.metadata)
        self.assertEqual(
            diff, [], "the database has drifted from the models; fix the models and reinstall"
        )

    # -- the product hierarchy ---------------------------------------------

    def test_full_hierarchy_and_task_numbers(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        sprint = add(
            self.conn,
            "sprints",
            workspace_id=w,
            project_id=a["project"],
            name="S1",
            status="active",
        )
        t1 = add(
            self.conn,
            "tasks",
            workspace_id=w,
            project_id=a["project"],
            work_item_id=a["item"],
            sprint_id=sprint,
            title="One",
            assignee_id=a["agent"],
        )
        add(
            self.conn,
            "tasks",
            workspace_id=w,
            project_id=a["project"],
            work_item_id=a["item"],
            title="Two",
            parent_task_id=t1,
        )
        numbers = (
            self.conn.execute(text("select number from tasks order by number")).scalars().all()
        )
        self.assertEqual(numbers, [1, 2])  # FLWN-1, FLWN-2

        add(
            self.conn,
            "comments",
            workspace_id=w,
            task_id=t1,
            author_id=a["human"],
            body="Looks good",
        )
        doc = add(self.conn, "docs", workspace_id=w, project_id=a["project"], title="Spec")
        block = add(self.conn, "doc_blocks", workspace_id=w, doc_id=doc, type="paragraph")
        add(self.conn, "comments", workspace_id=w, doc_id=doc, block_id=block, body="Clarify this")

    def test_voice_note_with_transcript_and_attachment(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        channel = add(
            self.conn, "channels", workspace_id=w, team_id=a["team"], type="team", name="eng"
        )
        message = add(
            self.conn,
            "messages",
            workspace_id=w,
            channel_id=channel,
            author_id=a["human"],
            kind="voice_note",
        )
        file = add(
            self.conn,
            "files",
            workspace_id=w,
            kind="voice_note",
            source="chat",
            name="note.opus",
            container="chat-media",
            blob_path=f"{w}/channels/{channel}/note.opus",
            duration_ms=4200,
        )
        add(self.conn, "attachments", workspace_id=w, file_id=file, message_id=message)
        add(
            self.conn,
            "transcripts",
            workspace_id=w,
            file_id=file,
            message_id=message,
            status="ready",
            full_text="we drop Redis",
        )

    def test_memory_decision_and_conflict(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        memory = add(
            self.conn,
            "memories",
            workspace_id=w,
            kind="decision",
            scope="team",
            team_id=a["team"],
            text="We do not use Redis",
            embedding=[0.1] * 1536,
        )
        self.conn.execute(
            insert(T("decisions")).values(
                workspace_id=w, memory_id=memory, statement="No Redis", area="infra"
            )
        )
        task = add(
            self.conn,
            "tasks",
            workspace_id=w,
            project_id=a["project"],
            work_item_id=a["item"],
            title="Add Redis cache",
        )
        add(
            self.conn,
            "decision_conflicts",
            workspace_id=w,
            decision_id=memory,
            task_id=task,
            explanation="Redis was ruled out",
        )
        add(
            self.conn,
            "chunks",
            workspace_id=w,
            source_type="doc",
            source_id=uuid.uuid4(),
            text="hello",
            embedding=[0.2] * 1536,
        )
        hits = self.conn.execute(
            text("select id from memories where tsv @@ plainto_tsquery('simple', 'redis')")
        ).all()
        self.assertEqual(len(hits), 1)  # keyword search works on the generated tsvector

    # -- isolation ----------------------------------------------------------

    def test_rows_cannot_reference_another_workspace(self):
        a, b = make_workspace(self.conn, "alpha"), make_workspace(self.conn, "beta")
        # a task in workspace A assigned to a member of workspace B
        self.raises_integrity(
            lambda: add(
                self.conn,
                "tasks",
                workspace_id=a["workspace_id"],
                project_id=a["project"],
                work_item_id=a["item"],
                title="x",
                assignee_id=b["human"],
            )
        )
        # a task in project A using a sprint that belongs to a different project
        other = add(
            self.conn, "projects", workspace_id=a["workspace_id"], key="OTHER", name="Other"
        )
        sprint = add(
            self.conn, "sprints", workspace_id=a["workspace_id"], project_id=other, name="S"
        )
        self.raises_integrity(
            lambda: add(
                self.conn,
                "tasks",
                workspace_id=a["workspace_id"],
                project_id=a["project"],
                work_item_id=a["item"],
                title="y",
                sprint_id=sprint,
            )
        )

    def test_row_level_security(self):
        a, b = make_workspace(self.conn, "alpha"), make_workspace(self.conn, "beta")
        for ws in (a, b):
            add(
                self.conn,
                "tasks",
                workspace_id=ws["workspace_id"],
                project_id=ws["project"],
                work_item_id=ws["item"],
                title="t",
            )
        self.tx.commit()  # RLS tests use their own sessions
        try:
            with ENGINE.begin() as admin:
                admin.execute(text("drop role if exists flwn_app"))
                admin.execute(text("create role flwn_app"))
                admin.execute(text("grant all on all tables in schema public to flwn_app"))
            count = lambda s: s.execute(text("select count(*) from tasks")).scalar_one()  # noqa: E731

            with workspace_session(ENGINE, str(a["workspace_id"])) as s:
                s.execute(text("set local role flwn_app"))
                self.assertEqual(count(s), 1)  # sees only its own workspace
                with self.assertRaises(DBAPIError):  # cannot write into another workspace
                    with s.begin_nested():
                        add(
                            s,
                            "tasks",
                            workspace_id=b["workspace_id"],
                            project_id=b["project"],
                            work_item_id=b["item"],
                            title="smuggled",
                        )
            with ENGINE.begin() as no_ctx:
                no_ctx.execute(text("set local role flwn_app"))
                self.assertEqual(count(no_ctx), 0)  # no workspace set: sees nothing
            with workspace_session(ENGINE, "", service=True) as s:
                s.execute(text("set local role flwn_app"))
                self.assertEqual(count(s), 2)  # trusted service sees both
        finally:
            with ENGINE.begin() as cleanup:
                cleanup.execute(text("delete from workspaces where slug in ('alpha', 'beta')"))
                cleanup.execute(text("delete from users where email like '%@example.com'"))

    def test_app_role_is_least_privileged_and_confined_to_its_workspace(self):
        from app.db.install import grant_app_role

        a, b = make_workspace(self.conn, "alpha"), make_workspace(self.conn, "beta")
        for ws in (a, b):
            add(self.conn, "memories", workspace_id=ws["workspace_id"], kind="fact", text="t")
        self.tx.commit()
        try:
            with ENGINE.begin() as admin:
                grant_app_role(admin, "flwn_app_role")
                flags = admin.execute(
                    text(
                        "select rolsuper, rolbypassrls, rolcanlogin from pg_roles where rolname = 'flwn_app_role'"
                    )
                ).one()
                self.assertEqual(tuple(flags), (False, False, False))  # no login without a password
            with workspace_session(ENGINE, str(a["workspace_id"])) as s:
                s.execute(text("set local role flwn_app_role"))
                # no WHERE clause: row-level security alone confines this to workspace alpha
                self.assertEqual(s.execute(text("select count(*) from memories")).scalar(), 1)
                with self.assertRaises(DBAPIError):  # and it cannot create tables or drop policies
                    with s.begin_nested():
                        s.execute(text("create table smuggled (id int)"))
        finally:
            with ENGINE.begin() as cleanup:
                cleanup.execute(text("delete from workspaces where slug in ('alpha', 'beta')"))
                cleanup.execute(text("delete from users where email like '%@example.com'"))
                cleanup.execute(text("drop owned by flwn_app_role"))
                cleanup.execute(text("drop role flwn_app_role"))

    # -- constraints and triggers -------------------------------------------

    def test_check_constraints(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        task = add(
            self.conn,
            "tasks",
            workspace_id=w,
            project_id=a["project"],
            work_item_id=a["item"],
            title="t",
        )
        # an AI member needs an agent kind and a name; a human needs a user and takes the name from it
        self.raises_integrity(
            lambda: add(self.conn, "members", workspace_id=w, type="AI", name="x")
        )
        self.raises_integrity(
            lambda: add(self.conn, "members", workspace_id=w, type="AI", agent_kind="security")
        )
        self.raises_integrity(lambda: add(self.conn, "members", workspace_id=w, type="HUMAN"))
        user = add(self.conn, "users", email="named@example.com", name="Named")
        self.raises_integrity(
            lambda: add(
                self.conn, "members", workspace_id=w, user_id=user, type="HUMAN", name="dup"
            )
        )
        # a comment has exactly one target
        self.raises_integrity(lambda: add(self.conn, "comments", workspace_id=w, body="none"))
        self.raises_integrity(
            lambda: add(
                self.conn,
                "comments",
                workspace_id=w,
                task_id=task,
                project_id=a["project"],
                body="two",
            )
        )
        # a file's blob must sit under its own workspace
        self.raises_integrity(
            lambda: add(
                self.conn,
                "files",
                workspace_id=w,
                kind="pdf",
                name="x.pdf",
                container="workspace-files",
                blob_path=f"{uuid.uuid4()}/x.pdf",
            )
        )
        # keys are unique regardless of case
        self.raises_integrity(
            lambda: add(self.conn, "projects", workspace_id=w, key="flwn", name="dup")
        )
        # bad enum value
        self.raises_integrity(
            lambda: add(
                self.conn,
                "tasks",
                workspace_id=w,
                project_id=a["project"],
                work_item_id=a["item"],
                title="t",
                status="bogus",
            )
        )

    def test_events_are_append_only_and_updated_at_moves(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        self.conn.execute(
            insert(T("events")).values(workspace_id=w, entity_type="task", action="created")
        )
        self.raises_integrity(lambda: self.conn.execute(text("update events set action = 'x'")))

        before = self.conn.execute(
            text("select updated_at from teams where id = :t"), {"t": a["team"]}
        ).scalar_one()
        self.conn.execute(text("select pg_sleep(0.01)"))
        self.conn.execute(text("update teams set name = 'Eng 2' where id = :t"), {"t": a["team"]})
        after = self.conn.execute(
            text("select updated_at from teams where id = :t"), {"t": a["team"]}
        ).scalar_one()
        self.assertGreater(after, before)

    def test_removing_a_member_keeps_their_events_but_nothing_else_can_change(self):
        a = make_workspace(self.conn, "alpha")
        w = a["workspace_id"]
        self.conn.execute(
            insert(T("events")).values(
                workspace_id=w, actor_id=a["agent"], entity_type="task", action="created"
            )
        )
        # rewriting the record is refused, even for the actor column alone when it names someone else
        self.raises_integrity(lambda: self.conn.execute(text("update events set action = 'x'")))
        self.raises_integrity(
            lambda: self.conn.execute(text("update events set actor_id = :m"), {"m": a["human"]})
        )
        # removing the member clears the actor and keeps the event
        self.conn.execute(text("delete from members where id = :m"), {"m": a["agent"]})
        row = self.conn.execute(
            text("select actor_id, action from events where workspace_id = :w"), {"w": w}
        ).one()
        self.assertEqual((row.actor_id, row.action), (None, "created"))

    def test_deleting_a_workspace_removes_everything(self):
        a = make_workspace(self.conn, "alpha")
        add(
            self.conn,
            "tasks",
            workspace_id=a["workspace_id"],
            project_id=a["project"],
            work_item_id=a["item"],
            title="t",
        )
        self.conn.execute(text("delete from workspaces where id = :w"), {"w": a["workspace_id"]})
        for table in ("members", "projects", "tasks", "teams"):
            left = self.conn.execute(
                text(f"select count(*) from {table} where workspace_id = :w"),
                {"w": a["workspace_id"]},
            ).scalar_one()
            self.assertEqual(left, 0, table)


if __name__ == "__main__":
    unittest.main()
