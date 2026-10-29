# Postman

`flwn-data-engine.openapi.json` is generated from the running app, so it never goes stale.
Regenerate it with `./run.sh openapi`, then in Postman use **Import** and pick the file.
Import `flwn-local.postman_environment.json` too and select the "Flwn local" environment.

Two kinds of caller (see `docs/MERGE_DECISIONS.md`, D29):

1. **Backend (service key).** Send `X-Data-API-Key: {{serviceKey}}` to `POST /workspaces`,
   `GET /workspaces`, `/users` and `POST /auth/tokens`.
2. **Everything else (token).** Mint one with the service key:

   ```
   POST {{baseUrl}}/auth/tokens
   X-Data-API-Key: {{serviceKey}}
   { "workspace_id": "{{workspaceId}}", "member_id": "{{memberId}}", "scopes": ["admin"] }
   ```

   Put the returned `token` into the environment, and send `Authorization: Bearer {{token}}`
   on every `/workspaces/{id}/...` route. The service key is refused on those routes.

## Business CRUD slice (for the product backend)

`flwn-business.openapi.json` holds only the core business CRUD: users, workspaces, members,
teams and planning (projects, work items, sprints, tasks). Everything else (collaboration, the
generated table routes, memory, files, meetings, atoms) and the schemas only those use are left
out, so the backend can generate its types from it. Regenerate with `./run.sh openapi-business`,
or fetch it live from `GET /openapi/business.json`. The tags it keeps are set in
`app/api/business_spec.py`.
