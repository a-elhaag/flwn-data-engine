# Files API

Files (documents, PDFs, images, voice notes, audio, video, meeting recordings, agent reports)
are stored in Azure Blob Storage. Rows in the `files` table describe them. The API hands out
short-lived signed links, so the bytes go straight between the client and Azure.

Needs `AZURE_STORAGE_ACCOUNT_URL`; without it every file route returns `503`.

Uploads record who uploaded (`uploaded_by`) and leave `created`, `uploaded` and `deleted` events in
the audit log. The acting member comes from the token, or from `X-Acting-Member-Id` when the
trusted backend calls (see [MEMORY_API.md](MEMORY_API.md#who-is-acting)).

## Upload in three steps

1. **Start.** `POST /workspaces/{ws}/files`

   ```json
   {"kind": "voice_note", "name": "standup.opus", "content_type": "audio/ogg",
    "size_bytes": 48211, "source": "chat", "project_id": null, "folder_id": null, "team_id": null}
   ```

   Returns `201` with `file_id`, `upload_url`, `method` (`PUT`), `headers` and `expires_at`.
2. **Send the bytes.** `PUT` them in **one request** to `upload_url` with exactly the returned
   headers (`x-ms-blob-type`, `x-ms-version`, `Content-Type`). The link works for one blob, for
   `UPLOAD_URL_TTL_SECONDS` (default 15 minutes), over HTTPS. It can create the blob but never
   replace it, read it, lease it or upload it in blocks.
3. **Complete.** `POST /workspaces/{ws}/files/{id}/complete`. The service checks the blob exists
   and that its size matches `size_bytes` and the limit for the kind. On success the file is
   `ready`. On a mismatch the blob is deleted, the file is marked `failed` and the call returns
   `422`. Calling it twice is safe.

## Routes

| Method | Path | Scope | Result |
| --- | --- | --- | --- |
| POST | `/workspaces/{ws}/files` | files:write | Upload ticket (201) |
| POST | `/workspaces/{ws}/files/{id}/complete` | files:write | The file, now `ready` |
| GET | `/workspaces/{ws}/files` | files:read | Page of files. `limit`, `offset`, `kind`, `project_id`, `folder_id` |
| GET | `/workspaces/{ws}/files/{id}` | files:read | One file |
| GET | `/workspaces/{ws}/files/{id}/download` | files:read | Signed read link, valid `DOWNLOAD_URL_TTL_SECONDS` (default 5 minutes) |
| DELETE | `/workspaces/{ws}/files/{id}` | files:delete | Hides the file and deletes its blob (204) |

Status codes: `404` unknown file or workspace, `409` not uploaded yet (complete, download),
`422` invalid input, too large, wrong size, or an unknown project, folder or team, `503` storage
not configured. The service key may call every route; agent tokens need the scope and their own
workspace.

## Where things go

| Container | Holds | Chosen when |
| --- | --- | --- |
| `workspace-files` | documents, PDFs, images, other | default |
| `chat-media` | voice notes, chat images and files | `source: chat` |
| `meeting-recordings` | recordings, moved to Cool after 30 days | `kind: recording` or `source: meeting` |
| `agent-reports` | reports written by agents | `kind: report` or `source: agent` |

A blob's path is `{workspace_id}/{file_id}/{name}`. The name is cleaned (no directories, no odd
characters), and the database refuses any path outside the file's own workspace.

## Size limits

report 5 MB, image and voice note 25 MB, PDF, document and other 100 MB, audio 500 MB, video
2000 MB, recording 4000 MB. Signed links cannot enforce a size, so `complete` does. Every upload
is a single request: Azure allows up to 5000 MiB per request for the pinned `x-ms-version`, but
uploads above 25 MB have not been tested here, and there is no resumable upload.

## Security

- The storage account has no public access and no account keys (shared-key access is disabled).
- Links are signed with a user delegation key from the service's Entra identity, which needs the
  **Storage Blob Data Contributor** role on the account.
- The upload link has **create-only** permission. Verified on live Azure: a link that also has
  `write` can overwrite the blob, take a lease and break the service's lease, so a file could be
  swapped after `complete` checked it. With create only, Azure refuses an overwrite (403
  `UnauthorizedBlobOverwrite`), a lease and a block upload, so verified bytes are the bytes served.
- Meeting recordings (`kind: recording` or `source: meeting`) can only be registered with the
  service key, so they cannot be created outside a consented meeting.
- Deleted blobs stay recoverable in Azure for 7 days.
- Browser uploads straight to Azure need a CORS rule on the storage account for the web app's
  origin. That is not set yet because the origin is not known.
