"""Build the Postman collection from the running app's OpenAPI. Run: ./run.sh postman"""

import json
import re
from pathlib import Path

from app.main import app

HERE = Path(__file__).parent
SPEC = app.openapi()
SCHEMAS = SPEC["components"]["schemas"]
ORDER = ["post", "get", "put", "patch", "delete"]
PUBLIC = ("/docs", "/openapi", "/explorer", "/auth/dev")
ALIASES = {"workspace_id": "workspaceId", "member_id": "memberId"}


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)


def var(name: str) -> str:
    return ALIASES.get(name) or camel(name)


def resolve(schema: dict) -> dict:
    while "$ref" in schema:
        schema = SCHEMAS[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def sample(schema: dict, depth: int = 0):
    schema = resolve(schema)
    if schema.get("examples"):
        return schema["examples"][0]
    if "example" in schema:
        return schema["example"]
    for alt in schema.get("anyOf", []) + schema.get("oneOf", []):
        if alt.get("type") != "null":
            return sample(alt, depth)
    kind = schema.get("type")
    if kind == "object" and depth < 3:
        props = schema.get("properties", {})
        return {k: sample(props[k], depth + 1) for k in schema.get("required", []) if k in props}
    if "enum" in schema:
        return schema["enum"][0]
    fmt = schema.get("format")
    return {
        "string": {"uuid": "{{" + "workspaceId" + "}}", "email": "user@example.com"}.get(
            fmt, "text"
        ),
        "integer": 1,
        "number": 1,
        "boolean": True,
        "array": [],
    }.get(kind)


def script(*lines: str) -> dict:
    return {"type": "text/javascript", "exec": list(lines)}


def event(*lines: str) -> list:
    return [{"listen": "test", "script": script(*lines)}]


def request(method, path, op, tests, body=None, name=None, acting_free=False) -> dict:
    url = "{{baseUrl}}" + re.sub(r"\{(\w+)\}", lambda m: "{{" + var(m.group(1)) + "}}", path)
    query = [
        {"key": p["name"], "value": str(sample(p["schema"]) or "")}
        for p in op.get("parameters", [])
        if p["in"] == "query" and p.get("required")
    ]
    headers = []
    if not path.startswith(PUBLIC):
        headers.append({"key": "X-Data-API-Key", "value": "{{serviceKey}}"})
    if path.startswith("/workspaces/{workspace_id}/") and not acting_free:
        headers.append({"key": "X-Acting-Member-Id", "value": "{{memberId}}"})
    if query:
        url += "?" + "&".join(f"{q['key']}={q['value']}" for q in query)
    req = {"method": method.upper(), "header": headers, "url": url}
    if body is not None:
        headers.append({"key": "Content-Type", "value": "application/json"})
        req["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    return {
        "name": name or f"{method.upper()} {op.get('summary') or path}",
        "request": req,
        "event": event(*tests),
    }


def status_test(codes: list[str], strict: bool = False) -> list[str]:
    ok = [int(c) for c in codes if c.startswith("2")]
    documented = sorted({int(c) for c in codes if c.isdigit()} | {401, 403, 404, 409})
    return [
        f'pm.test("status is documented or a standard client error", () => pm.expect({documented}).to.include(pm.response.code));',
        'pm.test("not a server error", () => pm.expect(pm.response.code).to.be.below(500));',
        'pm.test("json body when present", () => { if (pm.response.text()) pm.response.json(); });',
        *(
            [f'pm.test("succeeds", () => pm.expect({ok}).to.include(pm.response.code));']
            if strict
            else []
        ),
    ]


def remember(path: str) -> list[str]:
    """After a create, keep its id under the name the item route uses for it."""
    item = next(
        (
            p
            for p in SPEC["paths"]
            if p.startswith(path + "/{") and p.count("/") == path.count("/") + 1
        ),
        None,
    )
    if not item:
        return []
    name = var(re.search(r"\{(\w+)\}$", item).group(1))
    return [
        f'pm.test("remember {name}", () => pm.environment.set("{name}", pm.response.json().id));'
    ]


def setup_folder() -> dict:
    steps = [
        (
            "Create workspace",
            "post",
            "/workspaces",
            {"name": "Postman {{$timestamp}}", "slug": "pm-{{$timestamp}}"},
            ('const j = pm.response.json(); pm.environment.set("workspaceId", j.id);'),
        ),
        (
            "Create user",
            "post",
            "/users",
            {"email": "pm-{{$timestamp}}@example.com", "name": "Postman Tester"},
            ('pm.environment.set("userEmail", pm.response.json().email);'),
        ),
        (
            "Add owner member",
            "post",
            "/workspaces/{workspace_id}/members",
            {"type": "HUMAN", "role": "owner", "name": "Postman Tester", "email": "{{userEmail}}"},
            ('pm.environment.set("memberId", pm.response.json().id);'),
        ),
    ]
    items = []
    for name, method, path, body, save in steps:
        op = SPEC["paths"][path][method]
        r = request(
            method,
            path,
            op,
            status_test(list(op["responses"]), strict=True) + [save],
            body,
            name,
            acting_free=True,
        )
        r["event"][0]["script"]["exec"].insert(
            0, 'pm.test("created", () => pm.response.to.be.success);'
        )
        items.append(r)
    return {"name": "00 Setup (run first)", "item": items}


def build() -> dict:
    folders: dict[str, list] = {}
    for path, methods in SPEC["paths"].items():
        for method, op in methods.items():
            if method not in ORDER:
                continue
            body = None
            if "requestBody" in op:
                body = sample(op["requestBody"]["content"]["application/json"]["schema"])
            tests = status_test(list(op["responses"]))
            if method == "post" and any(c.startswith("2") for c in op["responses"]):
                tests += remember(path)
            folders.setdefault(op.get("tags", ["Other"])[0], []).append(
                (path, ORDER.index(method), request(method, path, op, tests, body))
            )
    items = [setup_folder()] + [
        {
            "name": tag,
            "item": [r for _, _, r in sorted(rows, key=lambda x: (x[0].count("/"), x[0], x[1]))],
        }
        for tag, rows in sorted(folders.items())
    ]
    return {
        "info": {
            "name": "Flwn data engine",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "description": "Generated by postman/build.py. Run 00 Setup first, then any folder. Setup asserts real success; the other requests assert the status is documented (or 401/403/404/409) and never 5xx (bodies are synthesized, so most answer 422/404 until you paste real ids).",
        },
        "item": items,
    }


if __name__ == "__main__":
    out = HERE / "flwn-data-engine.postman_collection.json"
    out.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {out} ({sum(len(f['item']) for f in build()['item'])} requests)")
