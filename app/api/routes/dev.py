"""Development sign-in: see app/api/dev_session.py. Hidden from the docs; 404 unless the database is local."""

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api import dev_session
from app.api.errors import api_error
from app.api.schemas.identity import Email, NonBlank

router = APIRouter(prefix="/dev", include_in_schema=False)


class DevSession(BaseModel):
    email: Email
    name: Annotated[NonBlank, Field()] = "Dev User"


@router.post("/session")
def dev_sign_in(request: Request, body: DevSession):
    if not (
        dev_session.enabled()
        and dev_session.loopback(request.client.host)
        and dev_session.direct(request.headers)
    ):
        raise api_error(404, "route_not_found", "Not Found")
    try:
        return dev_session.sign_in(body.email, body.name)
    except dev_session.RealUser as exc:
        raise api_error(409, exc.code, str(exc)) from None
