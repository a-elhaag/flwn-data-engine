"""Paging helper: services are asked for one row more than the page, to learn if more follow."""


def split_page(rows: list, limit: int) -> tuple[list, bool]:
    return rows[:limit], len(rows) > limit
