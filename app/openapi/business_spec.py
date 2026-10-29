"""The business slice of the OpenAPI document: what the product backend calls for its CRUD.

Users, workspaces, members, teams and planning (projects, work items, sprints, tasks). Every other
area (collaboration, the generated table routes, engine internals) is left out, and so is every
schema that only those areas reference, so the backend can generate its types from this document
alone.
"""

import copy

BUSINESS_TAGS = {"Users", "Workspaces", "Identity", "Planning"}

_REF = "#/components/schemas/"


def _refs(node) -> set[str]:
    if isinstance(node, dict):
        found = {node["$ref"].removeprefix(_REF)} if isinstance(node.get("$ref"), str) else set()
        for value in node.values():
            found |= _refs(value)
        return found
    if isinstance(node, list):
        return set().union(*map(_refs, node)) if node else set()
    return set()


def business_openapi(full: dict) -> dict:
    """Return a copy of `full` with only the business operations and the schemas they reach."""
    spec = copy.deepcopy(full)
    paths = {}
    for path, methods in spec["paths"].items():
        kept = {m: op for m, op in methods.items() if set(op.get("tags", [])) & BUSINESS_TAGS}
        if kept:
            paths[path] = kept
    spec["paths"] = paths

    schemas = spec.get("components", {}).get("schemas", {})
    reachable, pending = set(), _refs(paths)
    while pending:
        name = pending.pop()
        if name in reachable or name not in schemas:
            continue
        reachable.add(name)
        pending |= _refs(schemas[name])
    spec["components"]["schemas"] = {n: s for n, s in schemas.items() if n in reachable}

    used = {tag for ops in paths.values() for op in ops.values() for tag in op.get("tags", [])}
    spec["tags"] = [t for t in spec.get("tags", []) if t["name"] in used]
    spec["info"]["title"] = f"{spec['info']['title']} (business CRUD)"
    return spec
