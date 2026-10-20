"""SQL-only aggregate reads: no raw content, identifiers, or model-written summaries."""

from sqlalchemy import func, select

from app.atoms.access import AtomContext, aggregate_session, live_grants
from app.atoms.skills import _uuid
from app.db.models.atoms import AtomConnection
from app.db.models.collab import Doc, Message
from app.db.models.files import File
from app.db.models.meetings import Meeting
from app.db.models.memory import Memory
from app.db.models.planning import Project, Task

# Grouping arbitrary strings (titles, labels, JSON properties, toolkits) can disclose raw data.
RESOURCES = {
    "project": (Task, "project_id", {"status", "priority"}),
    "team": (Project, "team_id", {"status"}),
    "collection": (Doc, "collection_id", {"is_template", "is_locked"}),
    "channel": (Message, "channel_id", {"kind"}),
    "folder": (File, "folder_id", {"kind", "status"}),
    "memory": (Memory, "id", {"kind", "scope", "status"}),
    "meeting": (Meeting, "id", {"status"}),
    "connection": (AtomConnection, "id", {"status", "permission_ceiling"}),
}


class AggregateService:
    def __init__(self, workspace_id, actor, *, atom_id=None, run_id=None, bind=None):
        if atom_id is None or run_id is None:
            raise PermissionError("aggregate reads require an atom and run")
        self.context = AtomContext(
            str(_uuid(workspace_id)), str(_uuid(atom_id)), str(_uuid(run_id)), str(_uuid(actor))
        )
        self.bind = bind

    def read(self, resource_type, *, resource_id=None, group_by=None, trend=None):
        if resource_type not in RESOURCES:
            raise ValueError("unsupported aggregate resource")
        model, resource_field, fields = RESOURCES[resource_type]
        if group_by is not None and (not isinstance(group_by, str) or group_by not in fields):
            raise ValueError("unsupported aggregate grouping")
        if trend not in (None, "day", "week", "month"):
            raise ValueError("trend must be day, week, or month")
        target = _uuid(resource_id) if resource_id is not None else None
        grants = live_grants(self.context, bind=self.bind)
        relevant = [
            grant
            for grant in grants
            if grant["resource_type"] == resource_type
            and (
                target is None
                or grant["resource_id"] is None
                or str(grant["resource_id"]) == str(target)
            )
        ]
        if not relevant:
            raise PermissionError("an aggregate grant is required")
        for grant in relevant:
            constraints = grant["constraints"]
            if not isinstance(constraints, dict) or set(constraints) - {"labels", "since_days"}:
                raise PermissionError("unknown grant constraints")
            if "since_days" in constraints and (
                type(constraints["since_days"]) is not int or constraints["since_days"] < 0
            ):
                raise PermissionError("invalid since_days constraint")
            if "labels" in constraints and (
                not isinstance(constraints["labels"], list)
                or any(not isinstance(label, str) or not label for label in constraints["labels"])
            ):
                raise PermissionError("invalid labels constraint")
        table = model.__table__
        conditions = [
            model.workspace_id == _uuid(self.context.workspace_id),
            func.atom_row_allowed(table.name, func.to_jsonb(table.table_valued()), "summary"),
        ]
        if resource_type == "connection":
            conditions.append(
                func.atom_grant_matches(
                    resource_type, model.id, func.to_jsonb(table.table_valued()), "summary"
                )
            )
        if target is not None:
            conditions.append(getattr(model, resource_field) == target)
        if hasattr(model, "deleted_at"):
            conditions.append(model.deleted_at.is_(None))
        with aggregate_session(self.context, bind=self.bind) as session:
            result = {
                "count": session.scalar(select(func.count()).select_from(model).where(*conditions))
            }
            if group_by is not None:
                column = getattr(model, group_by)
                rows = session.execute(
                    select(column, func.count())
                    .where(*conditions)
                    .group_by(column)
                    .order_by(column)
                )
                result["groups"] = [{"value": value, "count": count} for value, count in rows]
            if trend is not None:
                bucket = func.date_trunc(trend, func.timezone("UTC", model.created_at))
                rows = session.execute(
                    select(bucket, func.count())
                    .where(*conditions)
                    .group_by(bucket)
                    .order_by(bucket)
                )
                result["trend"] = [
                    {"period": value.isoformat(), "count": count} for value, count in rows
                ]
            return result
