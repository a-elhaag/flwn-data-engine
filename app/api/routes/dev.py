"""Development sign-in: see app/dev_session.py. Hidden from the docs; 404 unless the database is local."""

from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import dev_session
from app.api.identity_schemas import Email, NonBlank

router = APIRouter(prefix="/dev", include_in_schema=False)


class DevSession(BaseModel):
    email: Email
    name: Annotated[NonBlank, Field()] = "Dev User"


@router.post("/session")
def dev_sign_in(body: DevSession):
    if not dev_session.enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if not tokens_enabled():
        raise HTTPException(status_code=503, detail="Set MEMORY_TOKEN_SECRET to issue tokens")
    return dev_session.sign_in(body.email, body.name)


def tokens_enabled() -> bool:
    from app.api import tokens

    return tokens.enabled()
