from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(settings.DATABASE_URL, pool_pre_ping=True)


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal(bind=get_engine()) as session:
        yield session
