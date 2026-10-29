"""Importing this package registers every model on Base.metadata."""

from app.db.models import (  # noqa: F401
    atoms,
    auth,
    collab,
    files,
    identity,
    meetings,
    memory,
    planning,
)
