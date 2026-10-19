"""Alembic environment. Migrations run as the admin role (DATABASE_ADMIN_URL), never the API role."""

from alembic import context
from sqlalchemy import create_engine

import app.db.models  # noqa: F401  (registers every model on Base.metadata)
from app.db.base import Base
from app.db.session import admin_database_url

target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = create_engine(admin_database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
