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

## Searching inside files

Once a file is verified, a background worker reads it, splits it into chunks, embeds them, and
stores them, so the contents can be searched. The file's `index_status` shows where it is:
`pending`, `indexing`, `done`, `failed` or `skipped` (with `index_error` saying why, or noting a
partial read), and `chunk_count` says how many chunks it produced.

`POST /workspaces/{ws}/files/search` (scope `files:read`) takes `{query, limit, kind?, project_id?}`
and returns the best chunks, each citing its file, page (PDFs) and heading path (Markdown):

```json
[{"file_id": "...", "file_name": "handbook.pdf", "page": 2, "heading_path": null,
  "text": "Refund policy: customers may cancel within 30 days...", "score": 0.91}]
```

The same search is available to agents as the MCP tool `files_search`. Search combines meaning
and exact words, then reranks the results, the same way memory recall does. Only live, verified,
fully indexed files are searched; deleting a file removes its chunks at once.
`POST /workspaces/{ws}/files/{id}/reindex` queues a file again (after a failure).

| What is uploaded | How it is read |
| --- | --- |
| Text, Markdown, JSON, YAML, CSV, source code | Decoded; Markdown headings become heading paths |
| PDF with a text layer | Read page by page (`pypdf`); a chunk never spans pages |
| Scanned PDF (no text layer) | Each page rendered to an image and read by the Cohere Parse model |
| Image (PNG, JPEG, WebP, GIF, BMP, TIFF) | Read by the Cohere Parse model, tables included |
| Audio, video, recordings, other types | Not indexed (`skipped`, with the reason) |

**Chat and meeting files are never indexed.** They can belong to a private channel or meeting, and
search would show their contents to the whole workspace. They need channel-level access control
first.

Limits: files over `INDEX_MAX_BYTES` (50 MB) or `INDEX_MAX_PAGES` (500) are skipped; at most
`INDEX_MAX_CHUNKS` (2000) chunks are kept (the rest is noted in `index_error`). The Parse model has a
very small quota (about one page per 10 seconds), so only the first `INDEX_MAX_SCANNED_PAGES` (10)
pages of a scan are read, and images and scans index slowly. Raising the Parse deployment's capacity
in Azure lifts that.

The worker runs inside the API process when `FILE_INDEXING=true` and storage is configured. Work is
queued in the database (`FOR UPDATE SKIP LOCKED`), so several replicas can run workers without
repeating a file, a restart loses nothing, and a claim left by a crashed worker is taken over after
`INDEX_STUCK_MINUTES` (15). Extracted text from user files is untrusted: results are data, not
instructions.

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
