"""Trusted workspace operations for comments, docs, and chat."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.db import events
from app.db.models.collab import (
    Channel,
    ChannelMember,
    Collection,
    Comment,
    Doc,
    DocBlock,
    Message,
)
from app.db.models.files import File
from app.db.models.identity import Member, Team, Workspace
from app.db.models.planning import Project, ProjectUpdate, Sprint, Task, WorkItem
from app.db.session import session_for

COMMENT_TARGETS = {
    "task": Task,
    "work_item": WorkItem,
    "project": Project,
    "project_update": ProjectUpdate,
    "sprint": Sprint,
    "doc": Doc,
}


class CollaborationNotFound(LookupError):
    pass


class CollaborationConflict(ValueError):
    pass


def _workspace(session, workspace_id):
    if (
        session.scalar(
            select(Workspace.id).where(
                Workspace.id == workspace_id,
                Workspace.deleted_at.is_(None),
            )
        )
        is None
    ):
        raise CollaborationNotFound("workspace not found")


def _active_member(session, workspace_id, member_id):
    if member_id is None:
        return None
    member_id = uuid.UUID(str(member_id))
    if (
        session.scalar(
            select(Member.id).where(
                Member.workspace_id == workspace_id,
                Member.id == member_id,
                Member.deleted_at.is_(None),
                Member.status == "active",
            )
        )
        is None
    ):
        raise CollaborationNotFound("active member not found")
    return member_id


def _doc(session, workspace_id, doc_id):
    row = session.scalar(
        select(Doc).where(
            Doc.workspace_id == workspace_id,
            Doc.id == uuid.UUID(str(doc_id)),
            Doc.deleted_at.is_(None),
            Doc.archived_at.is_(None),
        )
    )
    if row is None:
        raise CollaborationNotFound("doc not found")
    return row


def _channel(session, workspace_id, channel_id):
    row = session.scalar(
        select(Channel).where(
            Channel.workspace_id == workspace_id,
            Channel.id == uuid.UUID(str(channel_id)),
            Channel.archived_at.is_(None),
        )
    )
    if row is None:
        raise CollaborationNotFound("channel not found")
    return row


def _message(session, workspace_id, channel_id, message_id):
    row = session.scalar(
        select(Message).where(
            Message.workspace_id == workspace_id,
            Message.channel_id == channel_id,
            Message.id == uuid.UUID(str(message_id)),
            Message.deleted_at.is_(None),
        )
    )
    if row is None:
        raise CollaborationNotFound("message not found")
    return row


def _check_doc_refs(session, workspace_id, fields, doc_id=None):
    for key, model in (
        ("team_id", Team),
        ("project_id", Project),
        ("collection_id", Collection),
        ("cover_file_id", File),
    ):
        if fields.get(key) is not None:
            value = uuid.UUID(str(fields[key]))
            filters = [model.workspace_id == workspace_id, model.id == value]
            if hasattr(model, "deleted_at"):
                filters.append(model.deleted_at.is_(None))
            if session.scalar(select(model.id).where(*filters)) is None:
                raise CollaborationNotFound(f"{key.removesuffix('_id')} not found")
            fields[key] = value
    if fields.get("parent_doc_id") is not None:
        value = uuid.UUID(str(fields["parent_doc_id"]))
        if (
            value == doc_id
            or session.scalar(
                select(Doc.id).where(
                    Doc.workspace_id == workspace_id,
                    Doc.id == value,
                    Doc.deleted_at.is_(None),
                    Doc.archived_at.is_(None),
                )
            )
            is None
        ):
            raise CollaborationNotFound("parent doc not found")
        parent_id = value
        while parent_id is not None:
            parent_id = session.scalar(
                select(Doc.parent_doc_id).where(
                    Doc.workspace_id == workspace_id,
                    Doc.id == parent_id,
                    Doc.deleted_at.is_(None),
                    Doc.archived_at.is_(None),
                )
            )
            if parent_id == doc_id:
                raise CollaborationConflict("doc parent cannot create a cycle")
        fields["parent_doc_id"] = value


class CommentService:
    def __init__(self, workspace_id):
        self.workspace_id = uuid.UUID(str(workspace_id))

    def list(self, target_type, target_id, limit=50, offset=0):
        model = COMMENT_TARGETS[target_type]
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            target_id = uuid.UUID(str(target_id))
            filters = [model.workspace_id == self.workspace_id, model.id == target_id]
            if hasattr(model, "deleted_at"):
                filters.append(model.deleted_at.is_(None))
            if session.scalar(select(model.id).where(*filters)) is None:
                raise CollaborationNotFound(f"{target_type} not found")
            rows = session.scalars(
                select(Comment)
                .where(
                    Comment.workspace_id == self.workspace_id,
                    getattr(Comment, f"{target_type}_id") == target_id,
                    Comment.deleted_at.is_(None),
                )
                .order_by(Comment.created_at, Comment.id)
                .limit(limit)
                .offset(offset)
            )
            return list(rows)

    def get(self, comment_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = session.scalar(
                select(Comment).where(
                    Comment.workspace_id == self.workspace_id,
                    Comment.id == uuid.UUID(str(comment_id)),
                    Comment.deleted_at.is_(None),
                )
            )
            if row is None:
                raise CollaborationNotFound("comment not found")
            return row

    def create(
        self, *, target_type, target_id, block_id, parent_comment_id, body, body_json, author_id
    ):
        model = COMMENT_TARGETS[target_type]
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            target_id = uuid.UUID(str(target_id))
            filters = [model.workspace_id == self.workspace_id, model.id == target_id]
            if hasattr(model, "deleted_at"):
                filters.append(model.deleted_at.is_(None))
            if hasattr(model, "archived_at"):
                filters.append(model.archived_at.is_(None))
            if session.scalar(select(model.id).where(*filters)) is None:
                raise CollaborationNotFound(f"{target_type} not found")
            values = {f"{target_type}_id": target_id}
            if block_id is not None:
                block_id = uuid.UUID(str(block_id))
                if (
                    session.scalar(
                        select(DocBlock.id).where(
                            DocBlock.workspace_id == self.workspace_id,
                            DocBlock.doc_id == target_id,
                            DocBlock.id == block_id,
                        )
                    )
                    is None
                ):
                    raise CollaborationNotFound("doc block not found")
                values["block_id"] = block_id
            if parent_comment_id is not None:
                parent = self._get_comment(session, parent_comment_id)
                if getattr(parent, f"{target_type}_id") != target_id:
                    raise CollaborationConflict("reply must target the same resource as its parent")
                values["parent_comment_id"] = parent.id
            values.update(
                author_id=_active_member(session, self.workspace_id, author_id),
                body=body,
                body_json=body_json,
            )
            row = Comment(workspace_id=self.workspace_id, **values)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), None, "comment", row.id, "created")
            return row

    def update(self, comment_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = self._get_comment(session, comment_id)
            resolved = fields.pop("resolved", None)
            for key, value in fields.items():
                setattr(row, key, value)
            if "body" in fields or "body_json" in fields:
                row.edited_at = datetime.now(UTC)
            if resolved is not None:
                row.resolved_at = datetime.now(UTC) if resolved else None
                row.resolved_by = None
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                None,
                "comment",
                row.id,
                "updated",
                {"fields": sorted([*fields, *(["resolved"] if resolved is not None else [])])},
            )
            return row

    def delete(self, comment_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = self._get_comment(session, comment_id)
            row.deleted_at = datetime.now(UTC)
            session.flush()
            events.record(session, str(self.workspace_id), None, "comment", row.id, "deleted")

    def _get_comment(self, session, comment_id):
        row = session.scalar(
            select(Comment).where(
                Comment.workspace_id == self.workspace_id,
                Comment.id == uuid.UUID(str(comment_id)),
                Comment.deleted_at.is_(None),
            )
        )
        if row is None:
            raise CollaborationNotFound("comment not found")
        return row


class DocService:
    def __init__(self, workspace_id):
        self.workspace_id = uuid.UUID(str(workspace_id))

    def list(self, project_id=None, parent_doc_id=None, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            query = select(Doc).where(
                Doc.workspace_id == self.workspace_id,
                Doc.deleted_at.is_(None),
                Doc.archived_at.is_(None),
            )
            if project_id is not None:
                query = query.where(Doc.project_id == uuid.UUID(str(project_id)))
            if parent_doc_id is not None:
                query = query.where(Doc.parent_doc_id == uuid.UUID(str(parent_doc_id)))
            return list(
                session.scalars(
                    query.order_by(Doc.sort_order, Doc.created_at, Doc.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def get(self, doc_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return _doc(session, self.workspace_id, doc_id)

    def create(self, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            _check_doc_refs(session, self.workspace_id, fields)
            row = Doc(workspace_id=self.workspace_id, **fields)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), None, "doc", row.id, "created")
            return row

    def update(self, doc_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _doc(session, self.workspace_id, doc_id)
            _check_doc_refs(session, self.workspace_id, fields, doc_id=row.id)
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                None,
                "doc",
                row.id,
                "updated",
                {"fields": sorted(fields)},
            )
            return row

    def delete(self, doc_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _doc(session, self.workspace_id, doc_id)
            now = datetime.now(UTC)
            frontier = [row.id]
            seen = {row.id}
            while frontier:
                children = list(
                    session.scalars(
                        select(Doc).where(
                            Doc.workspace_id == self.workspace_id,
                            Doc.parent_doc_id.in_(frontier),
                            Doc.deleted_at.is_(None),
                        )
                    )
                )
                frontier = []
                for child in children:
                    if child.id not in seen:
                        seen.add(child.id)
                        child.deleted_at = now
                        frontier.append(child.id)
                        events.record(
                            session, str(self.workspace_id), None, "doc", child.id, "deleted"
                        )
            row.deleted_at = now
            session.flush()
            events.record(session, str(self.workspace_id), None, "doc", row.id, "deleted")

    def blocks(self, doc_id, limit=100, offset=0):
        with session_for(str(self.workspace_id)) as session:
            doc = _doc(session, self.workspace_id, doc_id)
            return list(
                session.scalars(
                    select(DocBlock)
                    .where(DocBlock.workspace_id == self.workspace_id, DocBlock.doc_id == doc.id)
                    .order_by(DocBlock.parent_block_id, DocBlock.sort_order, DocBlock.created_at)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def create_block(self, doc_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            doc = _doc(session, self.workspace_id, doc_id)
            parent_id = fields.get("parent_block_id")
            if parent_id is not None:
                parent_id = uuid.UUID(str(parent_id))
                if (
                    session.scalar(
                        select(DocBlock.id).where(
                            DocBlock.workspace_id == self.workspace_id,
                            DocBlock.doc_id == doc.id,
                            DocBlock.id == parent_id,
                        )
                    )
                    is None
                ):
                    raise CollaborationNotFound("parent block not found")
                fields["parent_block_id"] = parent_id
            row = DocBlock(workspace_id=self.workspace_id, doc_id=doc.id, **fields)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), None, "doc_block", row.id, "created")
            return row

    def update_block(self, doc_id, block_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            doc = _doc(session, self.workspace_id, doc_id)
            row = self._block(session, doc.id, block_id)
            parent_id = fields.get("parent_block_id")
            if parent_id is not None:
                parent_id = uuid.UUID(str(parent_id))
                if (
                    parent_id == row.id
                    or session.scalar(
                        select(DocBlock.id).where(
                            DocBlock.workspace_id == self.workspace_id,
                            DocBlock.doc_id == doc.id,
                            DocBlock.id == parent_id,
                        )
                    )
                    is None
                ):
                    raise CollaborationNotFound("parent block not found")
                ancestor_id = parent_id
                while ancestor_id is not None:
                    ancestor_id = session.scalar(
                        select(DocBlock.parent_block_id).where(
                            DocBlock.workspace_id == self.workspace_id,
                            DocBlock.doc_id == doc.id,
                            DocBlock.id == ancestor_id,
                        )
                    )
                    if ancestor_id == row.id:
                        raise CollaborationConflict("block parent cannot create a cycle")
                fields["parent_block_id"] = parent_id
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                None,
                "doc_block",
                row.id,
                "updated",
                {"fields": sorted(fields)},
            )
            return row

    def delete_block(self, doc_id, block_id):
        with session_for(str(self.workspace_id)) as session:
            doc = _doc(session, self.workspace_id, doc_id)
            row = self._block(session, doc.id, block_id)
            session.delete(row)
            session.flush()
            events.record(session, str(self.workspace_id), None, "doc_block", row.id, "deleted")

    def _block(self, session, doc_id, block_id):
        row = session.scalar(
            select(DocBlock).where(
                DocBlock.workspace_id == self.workspace_id,
                DocBlock.doc_id == doc_id,
                DocBlock.id == uuid.UUID(str(block_id)),
            )
        )
        if row is None:
            raise CollaborationNotFound("doc block not found")
        return row


class ChannelService:
    def __init__(self, workspace_id):
        self.workspace_id = uuid.UUID(str(workspace_id))

    def list(self, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return list(
                session.scalars(
                    select(Channel)
                    .where(
                        Channel.workspace_id == self.workspace_id,
                        Channel.archived_at.is_(None),
                    )
                    .order_by(Channel.created_at, Channel.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def get(self, channel_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return _channel(session, self.workspace_id, channel_id)

    def create(self, member_ids=None, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            for key, model in (("team_id", Team), ("project_id", Project)):
                if fields.get(key) is not None:
                    value = uuid.UUID(str(fields[key]))
                    filters = [model.workspace_id == self.workspace_id, model.id == value]
                    if hasattr(model, "deleted_at"):
                        filters.append(model.deleted_at.is_(None))
                    if session.scalar(select(model.id).where(*filters)) is None:
                        raise CollaborationNotFound(f"{key.removesuffix('_id')} not found")
                    fields[key] = value
            member_ids = {uuid.UUID(str(item)) for item in (member_ids or [])}
            self._validate_members(session, member_ids)
            row = Channel(workspace_id=self.workspace_id, **fields)
            session.add(row)
            session.flush()
            session.add_all(
                ChannelMember(
                    workspace_id=self.workspace_id, channel_id=row.id, member_id=member_id
                )
                for member_id in sorted(member_ids, key=str)
            )
            events.record(session, str(self.workspace_id), None, "channel", row.id, "created")
            return row

    def update(self, channel_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            row = _channel(session, self.workspace_id, channel_id)
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                None,
                "channel",
                row.id,
                "updated",
                {"fields": sorted(fields)},
            )
            return row

    def archive(self, channel_id):
        with session_for(str(self.workspace_id)) as session:
            row = _channel(session, self.workspace_id, channel_id)
            row.archived_at = datetime.now(UTC)
            session.flush()
            events.record(session, str(self.workspace_id), None, "channel", row.id, "archived")

    def replace_members(self, channel_id, member_ids):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id)
            ids = {uuid.UUID(str(item)) for item in member_ids}
            self._validate_members(session, ids)
            session.execute(
                delete(ChannelMember).where(
                    ChannelMember.workspace_id == self.workspace_id,
                    ChannelMember.channel_id == channel.id,
                )
            )
            session.add_all(
                ChannelMember(
                    workspace_id=self.workspace_id, channel_id=channel.id, member_id=member_id
                )
                for member_id in sorted(ids, key=str)
            )
            events.record(
                session,
                str(self.workspace_id),
                None,
                "channel",
                channel.id,
                "members_replaced",
                {"member_ids": sorted(map(str, ids))},
            )
            return channel

    def _validate_members(self, session, member_ids):
        if not member_ids:
            return
        active = set(
            session.scalars(
                select(Member.id).where(
                    Member.workspace_id == self.workspace_id,
                    Member.id.in_(member_ids),
                    Member.deleted_at.is_(None),
                    Member.status == "active",
                )
            )
        )
        if active != member_ids:
            raise CollaborationNotFound(
                "all channel members must be active members of this workspace"
            )


class MessageService:
    def __init__(self, workspace_id):
        self.workspace_id = uuid.UUID(str(workspace_id))

    def list(self, channel_id, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id)
            return list(
                session.scalars(
                    select(Message)
                    .where(
                        Message.workspace_id == self.workspace_id,
                        Message.channel_id == channel.id,
                        Message.deleted_at.is_(None),
                    )
                    .order_by(Message.created_at, Message.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def get(self, channel_id, message_id):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id)
            return self._get(session, channel.id, message_id)

    def create(self, channel_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id)
            fields["author_id"] = _active_member(
                session, self.workspace_id, fields.get("author_id")
            )
            parent_id = fields.get("parent_message_id")
            if parent_id is not None:
                parent = self._get(session, channel.id, parent_id)
                fields["parent_message_id"] = parent.id
            row = Message(workspace_id=self.workspace_id, channel_id=channel.id, **fields)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), None, "message", row.id, "created")
            return row

    def update(self, channel_id, message_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id)
            row = self._get(session, channel.id, message_id)
            pinned = fields.pop("pinned", None)
            for key, value in fields.items():
                setattr(row, key, value)
            if fields:
                row.edited_at = datetime.now(UTC)
            if pinned is not None:
                row.pinned_at = datetime.now(UTC) if pinned else None
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                None,
                "message",
                row.id,
                "updated",
                {"fields": sorted([*fields, *(["pinned"] if pinned is not None else [])])},
            )
            return row

    def delete(self, channel_id, message_id):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id)
            row = self._get(session, channel.id, message_id)
            row.deleted_at = datetime.now(UTC)
            session.flush()
            events.record(session, str(self.workspace_id), None, "message", row.id, "deleted")

    def _get(self, session, channel_id, message_id):
        return _message(session, self.workspace_id, channel_id, message_id)
