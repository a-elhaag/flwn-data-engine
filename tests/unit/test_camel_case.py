import pytest
from pydantic.alias_generators import to_camel

from schemas.sprint import SprintCreate
from schemas.task import TaskCreate, TaskRead


@pytest.mark.parametrize(
    ("snake", "camel"),
    [("sprint_id", "sprintId"), ("acceptance_criteria", "acceptanceCriteria"), ("name", "name")],
)
def test_to_camel(snake, camel):
    assert to_camel(snake) == camel


def test_request_model_aliases_are_camel_case():
    assert TaskCreate.model_fields["acceptance_criteria"].alias == "acceptanceCriteria"
    assert SprintCreate.model_fields["start_date"].alias == "startDate"


def test_response_model_dumps_camel_case_keys():
    keys = set(TaskRead.model_json_schema(by_alias=True)["properties"])
    assert {"sprintId", "workItemId", "acceptanceCriteria", "createdAt", "key"} <= keys
    assert not any("_" in k for k in keys)
