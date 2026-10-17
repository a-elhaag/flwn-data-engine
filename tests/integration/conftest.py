"""Integration test lifecycle: fresh migrated test database, truncate after each test,
an API client, and entity fixtures that create data through the API."""

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from config import settings
from db.base import Base
from main import create_app

import factories as f

START_HINT = "Start Postgres with `bash run.sh postgres` (or set TEST_DATABASE_URL)."


def _admin_engine(url):
    return create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session", autouse=True)
def database():
    url = make_url(settings.DATABASE_URL)
    name = url.database or ""
    # Safety guard: the tests TRUNCATE every table, so never touch a non-test database.
    if not name.endswith("_test"):
        pytest.exit(
            f"Refusing to run integration tests: database {name!r} does not end in "
            "'_test'. Point TEST_DATABASE_URL at a dedicated test database.",
            returncode=2,
        )
    try:
        admin = _admin_engine(url)
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
        admin.dispose()
    except Exception as exc:  # noqa: BLE001
        pytest.exit(
            f"Integration tests need Postgres but it is unreachable ({exc.__class__.__name__}). "
            + START_HINT,
            returncode=2,
        )
    # No ini file: env.py would otherwise call logging.fileConfig and disable loggers.
    cfg = Config()
    cfg.set_main_option("script_location", "migrations")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield name


@pytest.fixture(autouse=True)
def clean_tables(database):
    yield
    from db.session import get_engine

    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def app():
    return create_app()


@pytest.fixture
def api(app) -> f.Api:
    return f.Api(TestClient(app, headers={"X-Data-API-Key": settings.DATA_API_KEY}))


@pytest.fixture
def anon(app) -> f.Api:
    """A client that sends no credentials at all."""
    return f.Api(TestClient(app))


# --- entity fixtures (all created through the API) ----------------------------------


@pytest.fixture
def user(api):
    return f.make_user(api)


@pytest.fixture
def org(api) -> f.Org:
    return f.make_org(api)


@pytest.fixture
def other_org(api) -> f.Org:
    return f.make_org(api)


@pytest.fixture
def workspace(api, org):
    return f.make_workspace(api, org)


@pytest.fixture
def team(api, org, workspace):
    return f.make_team(api, org, workspace)


@pytest.fixture
def ai_member(api, org):
    return f.make_ai_member(api, org)


@pytest.fixture
def project(api, org, workspace):
    return f.make_project(api, org, workspace)


@pytest.fixture
def project_with_team(api, org, project, team):
    f.add_team_to_project(api, org, project, team)
    return project


@pytest.fixture
def sprint(api, org, project_with_team, team):
    return f.make_sprint(api, org, project_with_team, team)


@pytest.fixture
def work_item(api, org, project):
    return f.make_work_item(api, org, project)


@pytest.fixture
def task(api, org, project):
    return f.make_task(api, org, project)
