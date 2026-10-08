"""Importing this package registers every model on Base.metadata."""

from app.db.models import collab, files, identity, meetings, memory, planning  # noqa: F401
