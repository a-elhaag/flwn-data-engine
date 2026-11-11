"""Trusted workspace operations for comments, docs, and chat."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, delete, exists, false, or_, select, true

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
from app.domain.error_codes import DomainError, Invalid, InvalidReference
from app.domain.identity.viewer import Viewer

COMMENT_TARGETS = {
    "task": Task,
    "work_item": WorkItem,
    "project": Project,
    "project_update": ProjectUpdate,
    "sprint": Sprint,
    "doc": Doc,
}


class CollaborationNotFound(DomainError, LookupError):
    code = "collaboration_not_found"


class CollaborationConflict(DomainError, ValueError):
    code = "collaboration_conflict"


class CollaborationForbidden(DomainError, PermissionError):
    code = "collaboration_forbidden"


def _workspace(session, workspace_id):
    if (
        session.scalar(
            select(Workspace.id).where(
                Workspace.id == workspace_id,
            )
        )
        is None
    ):
        raise CollaborationNotFound("workspace not found", code="workspace_not_found")


def _active_member(session, workspace_id, member_id):
    if member_id is None:
        return None
    member_id = uuid.UUID(str(member_id))
    if (
        session.scalar(
            select(Member.id).where(
                Member.workspace_id == workspace_id,
                Member.id == member_id,
                Member.status == "active",
            )
        )
        is None
    ):
        raise InvalidReference("author")
    return member_id


def _doc(session, workspace_id, doc_id):
    row = session.scalar(
        select(Doc).where(
            Doc.workspace_id == workspace_id,
            Doc.id == uuid.UUID(str(doc_id)),
            Doc.archived_at.is_(None),
        )
    )
    if row is None:
        raise CollaborationNotFound("doc not found", code="doc_not_found")
    return row


def _channel_visible(viewer: Viewer):
    """Members see their channels. Direct and agent channels are members-only, even for an admin;
    an admin also sees private group, team and project channels; everyone sees the open ones."""
    is_member = (
        exists().where(
            ChannelMember.workspace_id == Channel.workspace_id,
            ChannelMember.channel_id == Channel.id,
            ChannelMember.member_id == viewer.member_id,
        )
        if viewer.member_id
        else false()
    )
    open_to_viewer = and_(
        Channel.type.notin_(("dm", "agent")),
        or_(Channel.is_private.is_(False), true() if viewer.admin else false()),
    )
    return or_(is_member, open_to_viewer)


def _channel(session, workspace_id, channel_id, viewer: Viewer):
    """A channel the viewer may see; one they may not is reported as missing."""
    row = session.scalar(
        select(Channel).where(
            Channel.workspace_id == workspace_id,
            Channel.id == uuid.UUID(str(channel_id)),
            Channel.archived_at.is_(None),
            _channel_visible(viewer),
        )
    )
    if row is None:
        raise CollaborationNotFound("channel not found", code="channel_not_found")
    return row


def _require_channel_owner(session, channel, viewer: Viewer):
    """Changing who is in a channel, or its privacy, is for its owners and administrators."""
    if viewer.admin:
        return
    owner = viewer.member_id and session.scalar(
        select(ChannelMember.member_id).where(
            ChannelMember.workspace_id == channel.workspace_id,
            ChannelMember.channel_id == channel.id,
            ChannelMember.member_id == viewer.member_id,
            ChannelMember.role == "owner",
        )
    )
    if not owner:
        raise CollaborationForbidden(
            "only a channel owner or an administrator can do this", code="channel_admin_required"
        )


def _message(session, workspace_id, channel_id, message_id):
    row = session.scalar(
        select(Message).where(
            Message.workspace_id == workspace_id,
            Message.channel_id == channel_id,
            Message.id == uuid.UUID(str(message_id)),
        )
    )
    if row is None:
        raise CollaborationNotFound("message not found", code="message_not_found")
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
            if session.scalar(select(model.id).where(*filters)) is None:
                raise InvalidReference(key.removesuffix("_id"))
            fields[key] = value
    if fields.get("parent_doc_id") is not None:
        value = uuid.UUID(str(fields["parent_doc_id"]))
        if (
            value == doc_id
            or session.scalar(
                select(Doc.id).where(
                    Doc.workspace_id == workspace_id,
                    Doc.id == value,
                    Doc.archived_at.is_(None),
                )
            )
            is None
        ):
            raise InvalidReference("parent_doc")
        parent_id = value
        while parent_id is not None:
            parent_id = session.scalar(
                select(Doc.parent_doc_id).where(
                    Doc.workspace_id == workspace_id,
                    Doc.id == parent_id,
                    Doc.archived_at.is_(None),
                )
            )
            if doc_id is not None and parent_id == doc_id:  # a new doc has no id yet
                raise CollaborationConflict(
                    "doc parent cannot create a cycle", code="doc_parent_cycle"
                )
        fields["parent_doc_id"] = value


class CommentService:
    def __init__(self, workspace_id, actor=None, viewer: Viewer | None = None):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.actor = str(actor) if actor else None  # who is acting, for the audit log
        self.viewer = viewer or Viewer(None, False)

    def _require_author(self, comment):
        mine = self.viewer.member_id
        if not self.viewer.admin and (mine is None or comment.author_id != mine):
            raise CollaborationForbidden(
                "only the author or an administrator can delete a comment",
                code="comment_delete_forbidden",
            )

    def list(self, target_type, target_id, limit=50, offset=0):
        model = COMMENT_TARGETS[target_type]
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            target_id = uuid.UUID(str(target_id))
            filters = [model.workspace_id == self.workspace_id, model.id == target_id]
            if session.scalar(select(model.id).where(*filters)) is None:
                raise CollaborationNotFound(
                    f"{target_type} not found", code=f"{target_type}_not_found"
                )
            rows = session.scalars(
                select(Comment)
                .where(
                    Comment.workspace_id == self.workspace_id,
                    getattr(Comment, f"{target_type}_id") == target_id,
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
                )
            )
            if row is None:
                raise CollaborationNotFound("comment not found", code="comment_not_found")
            return row

    def create(
        self, *, target_type, target_id, block_id, parent_comment_id, body, body_json, author_id
    ):
        model = COMMENT_TARGETS[target_type]
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            target_id = uuid.UUID(str(target_id))
            filters = [model.workspace_id == self.workspace_id, model.id == target_id]
            if hasattr(model, "archived_at"):
                filters.append(model.archived_at.is_(None))
            if session.scalar(select(model.id).where(*filters)) is None:
                raise InvalidReference("target")
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
                    raise InvalidReference("block")
                values["block_id"] = block_id
            if parent_comment_id is not None:
                parent = self._get_comment(session, parent_comment_id)
                if getattr(parent, f"{target_type}_id") != target_id:
                    raise CollaborationConflict(
                        "reply must target the same resource as its parent",
                        code="reply_target_mismatch",
                    )
                values["parent_comment_id"] = parent.id
            values.update(
                author_id=_active_member(session, self.workspace_id, author_id),
                body=body,
                body_json=body_json,
            )
            row = Comment(workspace_id=self.workspace_id, **values)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "comment", row.id, "created")
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
                self.actor,
                "comment",
                row.id,
                "updated",
                {"fields": sorted([*fields, *(["resolved"] if resolved is not None else [])])},
            )
            return row

    def delete(self, comment_id):
        """Delete a comment: its author or an administrator only. Its own replies and reactions
        go with it (ON DELETE CASCADE). Refused (409 comment_has_replies) while other members
        have replied: deleting would silently remove their words."""
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = self._get_comment(session, comment_id)
            self._require_author(row)
            # Every reply at any depth, not only direct ones: a deeper reply by someone else
            # would otherwise go with the comment.
            frontier, seen, others = [row.id], {row.id}, False
            while frontier and not others:
                replies = session.execute(
                    select(Comment.id, Comment.author_id).where(
                        Comment.workspace_id == self.workspace_id,
                        Comment.parent_comment_id.in_(frontier),
                    )
                ).all()
                frontier = []
                for reply_id, author_id in replies:
                    if reply_id in seen:
                        continue
                    seen.add(reply_id)
                    frontier.append(reply_id)
                    others = others or author_id != row.author_id
            if others:
                raise CollaborationConflict(
                    "the comment has replies from other members", code="comment_has_replies"
                )
            comment_id = row.id
            session.delete(row)  # its own replies and reactions go with it (ON DELETE CASCADE)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "comment", comment_id, "deleted"
            )

    def _get_comment(self, session, comment_id):
        row = session.scalar(
            select(Comment).where(
                Comment.workspace_id == self.workspace_id,
                Comment.id == uuid.UUID(str(comment_id)),
            )
        )
        if row is None:
            raise CollaborationNotFound("comment not found", code="comment_not_found")
        return row


class DocService:
    def __init__(self, workspace_id, actor=None):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.actor = str(actor) if actor else None  # who is acting, for the audit log

    def list(self, project_id=None, parent_doc_id=None, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            query = select(Doc).where(
                Doc.workspace_id == self.workspace_id,
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
            row = Doc(workspace_id=self.workspace_id, created_by=self.actor, **fields)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "doc", row.id, "created")
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
                self.actor,
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
            # Sub-pages go with it (ON DELETE CASCADE). Each one is still logged, with its title.
            frontier, seen, descendants = [row.id], {row.id}, []
            while frontier:
                children = list(
                    session.scalars(
                        select(Doc).where(
                            Doc.workspace_id == self.workspace_id,
                            Doc.parent_doc_id.in_(frontier),
                        )
                    )
                )
                frontier = []
                for child in children:
                    if child.id not in seen:
                        seen.add(child.id)
                        descendants.append(child)
                        frontier.append(child.id)
            snapshots = [(d.id, {"title": d.title, "parent": str(row.id)}) for d in descendants]
            snapshots.append((row.id, {"title": row.title}))
            session.delete(row)
            session.flush()
            for doc_id_, changes in snapshots:
                events.record(
                    session, str(self.workspace_id), self.actor, "doc", doc_id_, "deleted", changes
                )

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
                    raise InvalidReference("parent_block")
                fields["parent_block_id"] = parent_id
            row = DocBlock(workspace_id=self.workspace_id, doc_id=doc.id, **fields)
            session.add(row)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "doc_block", row.id, "created"
            )
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
                    raise InvalidReference("parent_block")
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
                        raise CollaborationConflict(
                            "block parent cannot create a cycle", code="block_parent_cycle"
                        )
                fields["parent_block_id"] = parent_id
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                self.actor,
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
            events.record(
                session, str(self.workspace_id), self.actor, "doc_block", row.id, "deleted"
            )

    def _block(self, session, doc_id, block_id):
        row = session.scalar(
            select(DocBlock).where(
                DocBlock.workspace_id == self.workspace_id,
                DocBlock.doc_id == doc_id,
                DocBlock.id == uuid.UUID(str(block_id)),
            )
        )
        if row is None:
            raise CollaborationNotFound("doc block not found", code="doc_block_not_found")
        return row


class ChannelService:
    def __init__(self, workspace_id, viewer: Viewer):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.viewer = viewer
        self.actor = str(viewer.member_id) if viewer.member_id else None

    def list(self, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return list(
                session.scalars(
                    select(Channel)
                    .where(
                        Channel.workspace_id == self.workspace_id,
                        Channel.archived_at.is_(None),
                        _channel_visible(self.viewer),
                    )
                    .order_by(Channel.created_at, Channel.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def get(self, channel_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return _channel(session, self.workspace_id, channel_id, self.viewer)

    def create(self, member_ids=None, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            for key, model in (("team_id", Team), ("project_id", Project)):
                if fields.get(key) is not None:
                    value = uuid.UUID(str(fields[key]))
                    filters = [model.workspace_id == self.workspace_id, model.id == value]
                    if session.scalar(select(model.id).where(*filters)) is None:
                        raise InvalidReference(key.removesuffix("_id"))
                    fields[key] = value
            member_ids = {uuid.UUID(str(item)) for item in (member_ids or [])}
            creator = self.viewer.member_id
            if creator:
                member_ids.add(creator)
            self._validate_members(session, member_ids)
            row = Channel(workspace_id=self.workspace_id, created_by=creator, **fields)
            session.add(row)
            session.flush()
            session.add_all(
                ChannelMember(
                    workspace_id=self.workspace_id,
                    channel_id=row.id,
                    member_id=member_id,
                    role="owner" if member_id == creator else "member",
                )
                for member_id in sorted(member_ids, key=str)
            )
            events.record(
                session,
                str(self.workspace_id),
                str(creator) if creator else None,
                "channel",
                row.id,
                "created",
            )
            return row

    def update(self, channel_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            row = _channel(session, self.workspace_id, channel_id, self.viewer)
            _require_channel_owner(session, row, self.viewer)
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                self.actor,
                "channel",
                row.id,
                "updated",
                {"fields": sorted(fields)},
            )
            return row

    def archive(self, channel_id):
        with session_for(str(self.workspace_id)) as session:
            row = _channel(session, self.workspace_id, channel_id, self.viewer)
            _require_channel_owner(session, row, self.viewer)
            row.archived_at = datetime.now(UTC)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "channel", row.id, "archived"
            )

    def replace_members(self, channel_id, member_ids):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id, self.viewer)
            _require_channel_owner(session, channel, self.viewer)
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
                self.actor,
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
                    Member.status == "active",
                )
            )
        )
        if active != member_ids:
            raise Invalid(
                "all channel members must be active members of this workspace",
                code="channel_member_inactive",
            )


class MessageService:
    def __init__(self, workspace_id, viewer: Viewer):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.viewer = viewer
        self.actor = str(viewer.member_id) if viewer.member_id else None

    def list(self, channel_id, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id, self.viewer)
            return list(
                session.scalars(
                    select(Message)
                    .where(
                        Message.workspace_id == self.workspace_id,
                        Message.channel_id == channel.id,
                    )
                    .order_by(Message.created_at, Message.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def get(self, channel_id, message_id):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id, self.viewer)
            return self._get(session, channel.id, message_id)

    def create(self, channel_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id, self.viewer)
            fields["author_id"] = _active_member(
                session, self.workspace_id, fields.get("author_id")
            )
            parent_id = fields.get("parent_message_id")
            if parent_id is not None:
                parent = session.scalar(
                    select(Message).where(
                        Message.workspace_id == self.workspace_id,
                        Message.channel_id == channel.id,
                        Message.id == uuid.UUID(str(parent_id)),
                    )
                )
                if parent is None:
                    raise InvalidReference("parent_message")
                fields["parent_message_id"] = parent.id
            row = Message(workspace_id=self.workspace_id, channel_id=channel.id, **fields)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "message", row.id, "created")
            return row

    def update(self, channel_id, message_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id, self.viewer)
            row = self._get(session, channel.id, message_id)
            self._require_author(row)
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
                self.actor,
                "message",
                row.id,
                "updated",
                {"fields": sorted([*fields, *(["pinned"] if pinned is not None else [])])},
            )
            return row

    def delete(self, channel_id, message_id):
        with session_for(str(self.workspace_id)) as session:
            channel = _channel(session, self.workspace_id, channel_id, self.viewer)
            row = self._get(session, channel.id, message_id)
            self._require_author(row)
            message_id = row.id
            session.delete(row)  # replies stay, unthreaded (ON DELETE SET NULL)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "message", message_id, "deleted"
            )

    def _get(self, session, channel_id, message_id):
        return _message(session, self.workspace_id, channel_id, message_id)

    def _require_author(self, message):
        mine = self.viewer.member_id
        if not self.viewer.admin and (mine is None or message.author_id != mine):
            raise CollaborationForbidden(
                "only the author or an administrator can change a message",
                code="message_edit_forbidden",
            )
