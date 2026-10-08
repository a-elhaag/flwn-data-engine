"""Draw the schema as an interactive graph: python -m app.db.erd [output.html]

Reads the SQLAlchemy models, so the picture cannot drift from the code. The output is a single
page (d3 from cdnjs) that can be opened locally or published as an Artifact.
"""

import json
import sys
from pathlib import Path

from sqlalchemy.dialects import postgresql

import app.db.models  # noqa: F401
from app.db.base import Base

DOMAINS = [
    ("identity", "People & teams"),
    ("planning", "Planning"),
    ("collab", "Docs, chat & comments"),
    ("files", "Files"),
    ("meetings", "Meetings"),
    ("memory", "Memory & search"),
    ("agents", "Agents & audit"),
]
# Models live in six modules; these tables belong to a different domain than their module.
OVERRIDES = {
    "agent_runs": "agents",
    "agent_reports": "agents",
    "approvals": "agents",
    "decision_conflicts": "agents",
    "llm_usage": "agents",
    "events": "agents",
    "attachments": "files",
}
BY_MODULE = {
    "identity": "identity",
    "planning": "planning",
    "collab": "collab",
    "files": "files",
    "meetings": "meetings",
    "memory": "memory",
}


def column_type(column) -> str:
    try:
        return column.type.compile(dialect=postgresql.dialect()).lower()
    except Exception:  # custom types that cannot compile generically
        return type(column.type).__name__.lower()


def build() -> dict:
    docs, modules = {}, {}
    for mapper in Base.registry.mappers:
        table = mapper.persist_selectable.name
        docs[table] = (mapper.class_.__doc__ or "").strip().split("\n\n")[0].replace("\n", " ")
        modules[table] = mapper.class_.__module__.rsplit(".", 1)[-1]

    tables, edges = [], {}
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        primary = {column.name for column in table.primary_key.columns}
        foreign = {column.name for fk in table.foreign_key_constraints for column in fk.columns}
        tables.append(
            {
                "name": table.name,
                "domain": OVERRIDES.get(table.name, BY_MODULE[modules[table.name]]),
                "doc": docs.get(table.name, ""),
                "columns": [
                    {
                        "name": column.name,
                        "type": column_type(column),
                        "pk": column.name in primary,
                        "fk": column.name in foreign,
                        "null": column.nullable and column.name not in primary,
                    }
                    for column in table.columns
                ],
            }
        )
        for fk in table.foreign_key_constraints:
            target = fk.referred_table.name
            via = fk.column_keys[-1]  # the column that carries the reference (not workspace_id)
            edge = edges.setdefault((table.name, target), [])
            if via not in edge:
                edge.append(via)
    return {
        "domains": [{"id": key, "label": label} for key, label in DOMAINS],
        "tables": tables,
        "edges": [{"source": s, "target": t, "via": via} for (s, t), via in sorted(edges.items())],
    }


def render() -> str:
    template = (Path(__file__).parent / "erd_template.html").read_text()
    return template.replace("__DATA__", json.dumps(build(), separators=(",", ":")))


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/schema-graph.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render())
    data = build()
    print(f"wrote {out}: {len(data['tables'])} tables, {len(data['edges'])} relationships")
