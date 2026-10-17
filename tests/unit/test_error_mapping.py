import asyncio
import json

import pytest
from pydantic.alias_generators import to_camel
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.exc import IntegrityError

from db.base import Base
from domain.errors import integrity_error_handler, map_constraint


def _all_constraint_and_unique_index_names() -> list[str]:
    names = []
    for table in Base.metadata.tables.values():
        names += [c.name for c in table.constraints]
        names += [i.name for i in table.indexes if i.unique]
    return names


def test_every_constraint_and_unique_index_has_a_mapping():
    names = _all_constraint_and_unique_index_names()
    assert len(names) > 50  # sanity: the metadata really was inspected
    assert None not in names, "every constraint must be named"
    unmapped = [n for n in names if map_constraint(str(n)) is None]
    assert unmapped == [], f"add these to domain/errors.py: {unmapped}"


def test_every_foreign_key_maps_to_its_first_local_column():
    for table in Base.metadata.tables.values():
        for c in table.constraints:
            if isinstance(c, ForeignKeyConstraint):
                error = map_constraint(str(c.name))
                assert (error.status, error.code) == (422, "INVALID_REFERENCE")
                assert error.field == to_camel(c.column_keys[0])


def test_duplicate_team_name_maps_to_409_team_already_exists():
    error = map_constraint("uq_teams_workspace_id_name_active")
    assert (error.status, error.code, error.field) == (409, "TEAM_ALREADY_EXISTS", "name")


def test_invalid_sprint_reference_maps_to_422_invalid_reference():
    error = map_constraint("fk_tasks_sprint_id_project_id_sprints")
    assert (error.status, error.code, error.field) == (422, "INVALID_REFERENCE", "sprintId")


def test_project_team_with_sprints_is_conflict_only_on_delete():
    name = "fk_sprints_project_id_team_id_project_teams"
    assert map_constraint(name, is_delete=True).code == "PROJECT_TEAM_HAS_SPRINTS"
    assert map_constraint(name, is_delete=False).code == "INVALID_REFERENCE"


@pytest.mark.parametrize(
    ("name", "field"),
    [("ck_tasks_done_completed", "completedAt"), ("ck_sprints_dates", "endDate"),
     ("ck_members_human_or_ai", "type"), ("ck_projects_key_format", None)],
)
def test_check_constraints_map_to_validation_error(name, field):
    error = map_constraint(name)
    assert (error.status, error.code, error.field) == (422, "VALIDATION_ERROR", field)


def test_unknown_constraint_name_is_unmapped():
    assert map_constraint("totally_unknown") is None
    assert map_constraint(None) is None


class _Diag:
    constraint_name = "totally_unknown"


class _Orig(Exception):
    diag = _Diag()


def test_unknown_integrity_error_returns_500_without_leaking_sql():
    exc = IntegrityError("INSERT INTO secret_table (x) VALUES (%s)", {"x": 1}, _Orig("boom"))
    response = asyncio.run(integrity_error_handler(None, exc))
    body = json.loads(response.body)
    assert response.status_code == 500
    assert body["code"] == "INTERNAL_ERROR"
    assert "INSERT" not in body["message"] and "secret_table" not in body["message"]
