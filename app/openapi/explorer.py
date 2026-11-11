"""The explorer: the schema as a diagram, with each table's endpoints listed under it.

Built at request time from the same sources as the rest of the docs: the model metadata (db/erd.py)
and the OpenAPI document. Nothing is copied by hand, so it cannot drift from the code.
"""

import json
from pathlib import Path

from app.db import erd

TEMPLATE = Path(__file__).parent / "explorer_template.html"


def table_for(path: str, tables: set[str]) -> str | None:
    """The table an endpoint works on: its last literal path segment that names a table.

    /projects/{id}/tasks -> tasks; /tasks/by-key/{key} -> tasks; /members/{id}/projects -> projects.
    Action routes such as /memories/recall have no table and return None.
    """
    literals = [seg.replace("-", "_") for seg in path.split("/") if seg and not seg.startswith("{")]
    for seg in reversed(literals):
        if seg in tables:
            return seg
    return None


def operations(openapi: dict, tables: set[str]) -> list[dict]:
    rows = []
    for path, methods in openapi["paths"].items():
        for method, op in methods.items():
            rows.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "summary": op.get("summary", ""),
                    "table": table_for(path, tables),
                }
            )
    return rows


def render(openapi: dict) -> str:
    schema = erd.build()
    tables = {t["name"] for t in schema["tables"]}
    payload = {
        "domains": schema["domains"],
        "tables": schema["tables"],
        "edges": schema["edges"],
        "operations": operations(openapi, tables),
    }
    # "<" is escaped so no summary can close the script tag early
    data = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    return TEMPLATE.read_text().replace("__DATA__", data)
