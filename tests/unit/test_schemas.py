import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from db.enums import (
    MemberRole, MemberStatus, Priority, ProjectStatus, SprintStatus, TaskStatus,
    TeamMemberRole, WorkItemStatus,
)  # fmt: skip
from schemas.member import MemberCreate, MemberUpdate
from schemas.organization import OrganizationCreate, OrganizationUpdate
from schemas.project import ProjectCreate, ProjectUpdate
from schemas.sprint import SprintCreate, SprintUpdate
from schemas.task import TaskCreate, TaskUpdate
from schemas.team import TeamCreate, TeamMemberCreate, TeamUpdate
from schemas.user import UserCreate, UserUpdate
from schemas.work_item import WorkItemCreate, WorkItemUpdate
from schemas.workspace import WorkspaceCreate, WorkspaceUpdate

U = str(uuid.uuid4())


def workspace(**kw):
    return WorkspaceCreate.model_validate({"name": "Dept", "createdById": U, **kw})


def task(**kw):
    return TaskCreate.model_validate({"title": "T", "createdById": U, **kw})


# --- generic request rules -------------------------------------------------------------


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError) as err:
        workspace(bogus=1)
    assert err.value.errors()[0]["type"] == "extra_forbidden"


def test_snake_case_names_work_too_because_populate_by_name():
    assert WorkspaceCreate(name="Dept", created_by_id=U).created_by_id == uuid.UUID(U)


def test_names_are_trimmed():
    assert workspace(name="  Dept  ").name == "Dept"


@pytest.mark.parametrize("field", ["description"])
def test_empty_optional_text_becomes_none(field):
    assert workspace(**{field: ""}).description is None
    assert workspace(**{field: "   "}).description is None


def test_empty_long_text_becomes_none():
    assert task(requirements="", acceptanceCriteria=" ").requirements is None


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_name_is_rejected(name):
    with pytest.raises(ValidationError):
        workspace(name=name)


@pytest.mark.parametrize(
    ("build", "limit"),
    [
        (lambda v: workspace(name=v), 100),
        (lambda v: task(title=v), 200),
        (lambda v: workspace(description=v), 5000),
        (lambda v: task(requirements=v), 10000),
        (lambda v: task(acceptanceCriteria=v), 10000),
    ],
)
def test_length_limits(build, limit):
    build("a" * limit)  # exactly at the limit is fine
    with pytest.raises(ValidationError):
        build("a" * (limit + 1))


# --- email ------------------------------------------------------------------------------


def test_email_is_lowercased():
    assert UserCreate(name="Ghost", email="Ghost@Flwn.AI").email == "ghost@flwn.ai"


@pytest.mark.parametrize("email", ["not-an-email", "a@", "@b.io", "", "a" * 250 + "@x.io"])
def test_invalid_email_is_rejected(email):
    with pytest.raises(ValidationError):
        UserCreate(name="Ghost", email=email)


# --- datetimes --------------------------------------------------------------------------


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError):
        task(dueDate="2026-11-03T17:00:00")


def test_datetime_with_offset_is_normalised_to_utc():
    value = task(dueDate="2026-11-03T17:00:00+03:00").due_date
    assert value == datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)
    assert value.utcoffset() == timedelta(0)


# --- project key ------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["FLWN", "AB", "ABCDEFGHIJ"])
def test_valid_project_key(key):
    assert ProjectCreate.model_validate({"key": key, "name": "P", "createdById": U}).key == key


@pytest.mark.parametrize("key", ["flwn", "A", "ABCDEFGHIJK", "FL1", "FL-N", ""])
def test_invalid_project_key_is_rejected_not_uppercased(key):
    with pytest.raises(ValidationError):
        ProjectCreate.model_validate({"key": key, "name": "P", "createdById": U})


# --- cross-field rules --------------------------------------------------------------------


def test_human_member_requires_user_id():
    with pytest.raises(ValidationError):
        MemberCreate.model_validate({"type": "HUMAN"})


@pytest.mark.parametrize("extra", [{"name": "x"}, {"email": "x@y.io"}])
def test_human_member_must_not_have_name_or_email(extra):
    with pytest.raises(ValidationError):
        MemberCreate.model_validate({"type": "HUMAN", "userId": U, **extra})


def test_ai_member_requires_name():
    with pytest.raises(ValidationError):
        MemberCreate.model_validate({"type": "AI"})


def test_ai_member_must_not_have_user_id():
    with pytest.raises(ValidationError):
        MemberCreate.model_validate({"type": "AI", "name": "Ghost", "userId": U})


def test_ai_member_email_is_optional_and_lowercased():
    assert MemberCreate.model_validate({"type": "AI", "name": "G"}).email is None
    assert MemberCreate.model_validate({"type": "AI", "name": "G", "email": "G@X.io"}).email == "g@x.io"


def test_sprint_end_before_start_is_rejected():
    with pytest.raises(ValidationError):
        SprintCreate.model_validate(
            {"name": "S", "createdById": U, "startDate": "2026-02-02", "endDate": "2026-02-01"}
        )


def test_sprint_same_day_or_one_date_only_is_accepted():
    both = SprintCreate.model_validate(
        {"name": "S", "createdById": U, "startDate": "2026-02-02", "endDate": "2026-02-02"}
    )
    assert both.start_date == date(2026, 2, 2)
    SprintCreate.model_validate({"name": "S", "createdById": U, "endDate": "2026-02-01"})


def test_task_done_without_completed_at_is_rejected():
    with pytest.raises(ValidationError):
        task(status="DONE")


def test_task_completed_at_without_done_is_rejected():
    with pytest.raises(ValidationError):
        task(completedAt="2026-01-01T00:00:00Z")


def test_task_done_with_completed_at_is_accepted():
    assert task(status="DONE", completedAt="2026-01-01T00:00:00Z").status == TaskStatus.DONE


# --- Create defaults are the real values --------------------------------------------------


def test_create_defaults_are_real_values_not_none():
    member = MemberCreate.model_validate({"type": "AI", "name": "G"})
    assert (member.role, member.status) == (MemberRole.MEMBER, MemberStatus.ACTIVE)
    assert TeamMemberCreate(member_id=U).role == TeamMemberRole.MEMBER
    assert ProjectCreate.model_validate({"key": "AB", "name": "P", "createdById": U}).status == ProjectStatus.ACTIVE
    wi = WorkItemCreate.model_validate({"title": "W", "type": "BUG", "createdById": U})
    assert (wi.priority, wi.status) == (Priority.MEDIUM, WorkItemStatus.OPEN)
    assert SprintCreate.model_validate({"name": "S", "createdById": U}).status == SprintStatus.PLANNED
    t = task()
    assert (t.priority, t.status) == (Priority.MEDIUM, TaskStatus.TODO)


def test_create_dump_contains_defaults_so_inserts_never_bind_null():
    dump = MemberCreate.model_validate({"type": "AI", "name": "G"}).model_dump()
    assert dump["role"] == MemberRole.MEMBER and dump["status"] == MemberStatus.ACTIVE


def test_organization_creator_member_role_defaults_to_owner():
    org = OrganizationCreate.model_validate({"name": "O", "createdById": U})
    assert org.creator_member.role == MemberRole.OWNER


# --- Update: not sent vs null -------------------------------------------------------------


def test_update_distinguishes_not_sent_from_null():
    assert TaskUpdate.model_validate({}).model_fields_set == set()
    sent_null = TaskUpdate.model_validate({"description": None})
    assert sent_null.model_fields_set == {"description"}
    assert sent_null.description is None


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (UserUpdate, "name"), (UserUpdate, "email"),
        (WorkspaceUpdate, "name"), (WorkspaceUpdate, "archived"),
        (OrganizationUpdate, "name"), (TeamUpdate, "name"),
        (ProjectUpdate, "name"), (ProjectUpdate, "status"), (ProjectUpdate, "archived"),
        (MemberUpdate, "role"), (MemberUpdate, "status"),
        (WorkItemUpdate, "title"), (WorkItemUpdate, "type"), (WorkItemUpdate, "priority"),
        (WorkItemUpdate, "status"),
        (SprintUpdate, "name"), (SprintUpdate, "status"),
        (TaskUpdate, "title"), (TaskUpdate, "priority"), (TaskUpdate, "status"),
    ],
)  # fmt: skip
def test_update_rejects_null_for_not_null_fields(model, field):
    with pytest.raises(ValidationError):
        model.model_validate({field: None})


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (TaskUpdate, "description"), (TaskUpdate, "assigneeId"), (TaskUpdate, "sprintId"),
        (TaskUpdate, "workItemId"), (TaskUpdate, "dueDate"), (TaskUpdate, "completedAt"),
        (SprintUpdate, "startDate"), (SprintUpdate, "endDate"), (SprintUpdate, "goal"),
        (MemberUpdate, "workspaceId"), (WorkspaceUpdate, "description"),
    ],
)  # fmt: skip
def test_update_accepts_null_for_nullable_fields(model, field):
    assert model.model_validate({field: None}).model_fields_set


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (ProjectUpdate, "key"), (MemberUpdate, "type"), (MemberUpdate, "userId"),
        (TaskUpdate, "createdById"), (WorkItemUpdate, "createdById"),
        (SprintUpdate, "createdById"), (WorkspaceUpdate, "createdById"),
        (TeamUpdate, "createdById"), (ProjectUpdate, "createdById"),
        (TaskUpdate, "projectId"), (SprintUpdate, "teamId"), (TeamUpdate, "workspaceId"),
        (TaskUpdate, "number"),
    ],
)  # fmt: skip
def test_update_rejects_immutable_fields(model, field):
    with pytest.raises(ValidationError) as err:
        model.model_validate({field: U if field.endswith("Id") else "X"})
    assert err.value.errors()[0]["type"] == "extra_forbidden"


def test_create_schemas_do_not_accept_server_owned_fields():
    for field in ("id", "organizationId", "createdAt", "number", "taskCounter", "archivedAt"):
        with pytest.raises(ValidationError):
            task(**{field: "x"})
