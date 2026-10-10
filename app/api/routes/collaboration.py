"""Trusted CRUD for comments, structured docs, and workspace chat."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError

from app.api.auth import WorkspaceId, service_only
from app.api.collab_schemas import (
    BlockCreate,
    BlockOut,
    BlockPage,
    BlockUpdate,
    ChannelCreate,
    ChannelMembersUpdate,
    ChannelOut,
    ChannelPage,
    ChannelUpdate,
    CommentCreate,
    CommentOut,
    CommentPage,
    CommentUpdate,
    DocCreate,
    DocOut,
    DocPage,
    DocUpdate,
    MessageCreate,
    MessageOut,
    MessagePage,
    MessageUpdate,
)
from app.collaboration.service import (
    ChannelService,
    CollaborationConflict,
    CollaborationNotFound,
    CommentService,
    DocService,
    MessageService,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", dependencies=[Depends(service_only)])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except CollaborationNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except CollaborationConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except IntegrityError:
        raise HTTPException(409, "resource conflicts with an existing record") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/comments", status_code=201, response_model=CommentOut)
def create_comment(workspace_id: WorkspaceId, body: CommentCreate):
    return _call(CommentService(str(workspace_id)).create, **body.model_dump())


@router.get("/comments", response_model=CommentPage)
def list_comments(
    workspace_id: WorkspaceId,
    target_type: Literal["task", "work_item", "project", "project_update", "sprint", "doc"],
    target_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(
        CommentService(str(workspace_id)).list,
        target_type,
        str(target_id),
        limit,
        offset,
    )
    return CommentPage(items=items, limit=limit, offset=offset)


@router.get("/comments/{comment_id}", response_model=CommentOut)
def get_comment(workspace_id: WorkspaceId, comment_id: UUID):
    return _call(CommentService(str(workspace_id)).get, str(comment_id))


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def update_comment(workspace_id: WorkspaceId, comment_id: UUID, body: CommentUpdate):
    return _call(
        CommentService(str(workspace_id)).update,
        str(comment_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(workspace_id: WorkspaceId, comment_id: UUID):
    _call(CommentService(str(workspace_id)).delete, str(comment_id))
    return Response(status_code=204)


@router.post("/docs", status_code=201, response_model=DocOut)
def create_doc(workspace_id: WorkspaceId, body: DocCreate):
    return _call(DocService(str(workspace_id)).create, **body.model_dump())


@router.get("/docs", response_model=DocPage)
def list_docs(
    workspace_id: WorkspaceId,
    project_id: UUID | None = None,
    parent_doc_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(
        DocService(str(workspace_id)).list,
        str(project_id) if project_id else None,
        str(parent_doc_id) if parent_doc_id else None,
        limit,
        offset,
    )
    return DocPage(items=items, limit=limit, offset=offset)


@router.get("/docs/{doc_id}", response_model=DocOut)
def get_doc(workspace_id: WorkspaceId, doc_id: UUID):
    return _call(DocService(str(workspace_id)).get, str(doc_id))


@router.patch("/docs/{doc_id}", response_model=DocOut)
def update_doc(workspace_id: WorkspaceId, doc_id: UUID, body: DocUpdate):
    return _call(
        DocService(str(workspace_id)).update,
        str(doc_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/docs/{doc_id}", status_code=204)
def delete_doc(workspace_id: WorkspaceId, doc_id: UUID):
    _call(DocService(str(workspace_id)).delete, str(doc_id))
    return Response(status_code=204)


@router.get("/docs/{doc_id}/blocks", response_model=BlockPage)
def list_doc_blocks(
    workspace_id: WorkspaceId,
    doc_id: UUID,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(DocService(str(workspace_id)).blocks, str(doc_id), limit, offset)
    return BlockPage(items=items, limit=limit, offset=offset)


@router.post("/docs/{doc_id}/blocks", status_code=201, response_model=BlockOut)
def create_doc_block(workspace_id: WorkspaceId, doc_id: UUID, body: BlockCreate):
    return _call(DocService(str(workspace_id)).create_block, str(doc_id), **body.model_dump())


@router.patch("/docs/{doc_id}/blocks/{block_id}", response_model=BlockOut)
def update_doc_block(workspace_id: WorkspaceId, doc_id: UUID, block_id: UUID, body: BlockUpdate):
    return _call(
        DocService(str(workspace_id)).update_block,
        str(doc_id),
        str(block_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/docs/{doc_id}/blocks/{block_id}", status_code=204)
def delete_doc_block(workspace_id: WorkspaceId, doc_id: UUID, block_id: UUID):
    _call(DocService(str(workspace_id)).delete_block, str(doc_id), str(block_id))
    return Response(status_code=204)


@router.post("/channels", status_code=201, response_model=ChannelOut)
def create_channel(workspace_id: WorkspaceId, body: ChannelCreate):
    data = body.model_dump(exclude={"member_ids"})
    return _call(
        ChannelService(str(workspace_id)).create,
        member_ids=body.member_ids,
        **data,
    )


@router.get("/channels", response_model=ChannelPage)
def list_channels(
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(ChannelService(str(workspace_id)).list, limit, offset)
    return ChannelPage(items=items, limit=limit, offset=offset)


@router.get("/channels/{channel_id}", response_model=ChannelOut)
def get_channel(workspace_id: WorkspaceId, channel_id: UUID):
    return _call(ChannelService(str(workspace_id)).get, str(channel_id))


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
def update_channel(workspace_id: WorkspaceId, channel_id: UUID, body: ChannelUpdate):
    return _call(
        ChannelService(str(workspace_id)).update,
        str(channel_id),
        **body.model_dump(exclude_unset=True),
    )


@router.put("/channels/{channel_id}/members", response_model=ChannelOut)
def replace_channel_members(
    workspace_id: WorkspaceId, channel_id: UUID, body: ChannelMembersUpdate
):
    return _call(
        ChannelService(str(workspace_id)).replace_members,
        str(channel_id),
        body.member_ids,
    )


@router.delete("/channels/{channel_id}", status_code=204)
def archive_channel(workspace_id: WorkspaceId, channel_id: UUID):
    _call(ChannelService(str(workspace_id)).archive, str(channel_id))
    return Response(status_code=204)


@router.post("/channels/{channel_id}/messages", status_code=201, response_model=MessageOut)
def create_message(workspace_id: WorkspaceId, channel_id: UUID, body: MessageCreate):
    return _call(MessageService(str(workspace_id)).create, str(channel_id), **body.model_dump())


@router.get("/channels/{channel_id}/messages", response_model=MessagePage)
def list_messages(
    workspace_id: WorkspaceId,
    channel_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(MessageService(str(workspace_id)).list, str(channel_id), limit, offset)
    return MessagePage(items=items, limit=limit, offset=offset)


@router.get("/channels/{channel_id}/messages/{message_id}", response_model=MessageOut)
def get_message(workspace_id: WorkspaceId, channel_id: UUID, message_id: UUID):
    return _call(MessageService(str(workspace_id)).get, str(channel_id), str(message_id))


@router.patch("/channels/{channel_id}/messages/{message_id}", response_model=MessageOut)
def update_message(
    workspace_id: WorkspaceId, channel_id: UUID, message_id: UUID, body: MessageUpdate
):
    return _call(
        MessageService(str(workspace_id)).update,
        str(channel_id),
        str(message_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/channels/{channel_id}/messages/{message_id}", status_code=204)
def delete_message(workspace_id: WorkspaceId, channel_id: UUID, message_id: UUID):
    _call(MessageService(str(workspace_id)).delete, str(channel_id), str(message_id))
    return Response(status_code=204)
