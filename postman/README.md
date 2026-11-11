# Postman

- `flwn-data-engine.postman_collection.json`: every route, one folder per tag, generated from the
  running app by `build.py`. Regenerate with `./run.sh postman`.
- `flwn-local.postman_environment.json` and `flwn-azure.postman_environment.json` (the deployed container): `baseUrl`, `serviceKey` and the ids the collection fills in. Put the service key in Postman, never in the file.
- `flwn-business.openapi.json`: only the core business CRUD (users, workspaces, members, teams,
  planning), for the product backend to generate types from. Regenerate with
  `./run.sh openapi-business`, or fetch it live from `GET /openapi/business.json`. The tags it
  keeps are set in `app/openapi/business_spec.py`.

## Use

Import the collection and the environment, select "Flwn local", set `serviceKey`, then run
**00 Setup** first: it creates a workspace, a user and an owner member and stores `workspaceId`
and `memberId`. Every request sends `X-Data-API-Key`; workspace routes also send
`X-Acting-Member-Id` so the service key acts as that member.

## What the tests assert

- Setup: real success statuses.
- Every other request: the status is documented in the OpenAPI (or 401/403/404/409), the body is
  JSON, and the response is never 5xx. Request bodies are synthesized from the schemas, so most
  of them answer 422 or 404 until you paste real ids into the environment.
- POST creates store the new `id` under the name the item route uses, so later requests in a
  folder can reuse it.

CLI run (needs a running app): `npx newman run postman/flwn-data-engine.postman_collection.json -e postman/flwn-local.postman_environment.json`.
