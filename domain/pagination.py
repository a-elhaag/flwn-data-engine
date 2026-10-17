from typing import Generic, TypeVar

from schemas.common import ResponseModel

T = TypeVar("T")


class Page(ResponseModel, Generic[T]):
    items: list[T]
    limit: int
    offset: int
    has_more: bool


def make_page(rows: list, has_more: bool, limit: int, offset: int) -> dict:
    """Plain dict; FastAPI validates it (from attributes) against Page[Read]."""
    return {"items": rows, "limit": limit, "offset": offset, "has_more": has_more}
