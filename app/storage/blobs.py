"""Azure Blob Storage: short-lived signed links so clients upload and download directly.

Bytes never pass through the API. Shared-key access is disabled on the storage account, so no
account keys exist anywhere: links are signed with a user delegation key obtained through the
service's Entra identity (DefaultAzureCredential: the managed identity on Azure, `az login`
locally). That identity needs the "Storage Blob Data Contributor" role on the account.
"""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas

from app.config import settings
from app.storage.errors import StorageNotConfigured

# One container per kind of content, so lifecycle and access rules can differ.
WORKSPACE_FILES = "workspace-files"
CHAT_MEDIA = "chat-media"
MEETING_RECORDINGS = "meeting-recordings"
AGENT_REPORTS = "agent-reports"

KEY_LIFETIME = timedelta(hours=6)
KEY_REFRESH_BEFORE = timedelta(minutes=30)


@dataclass(frozen=True)
class BlobInfo:
    size: int
    content_type: str | None


class BlobStorage:
    def __init__(self, account_url: str, credential=None):
        self._client = BlobServiceClient(
            account_url, credential=credential or DefaultAzureCredential()
        )
        self._key = None
        self._key_expires = datetime.min.replace(tzinfo=UTC)
        self._lock = threading.Lock()

    def _delegation_key(self):
        with self._lock:
            now = datetime.now(UTC)
            if self._key is None or now > self._key_expires - KEY_REFRESH_BEFORE:
                self._key_expires = now + KEY_LIFETIME
                self._key = self._client.get_user_delegation_key(
                    now - timedelta(minutes=5), self._key_expires
                )
            return self._key

    def _signed_url(
        self, container: str, path: str, permission: BlobSasPermissions, ttl: int, **options
    ) -> str:
        now = datetime.now(UTC)
        token = generate_blob_sas(
            account_name=self._client.account_name,
            container_name=container,
            blob_name=path,
            user_delegation_key=self._delegation_key(),
            permission=permission,
            start=now - timedelta(minutes=5),  # tolerate clock skew
            expiry=now + timedelta(seconds=ttl),
            protocol="https",
            **options,
        )
        return f"{self._client.get_blob_client(container, path).url}?{token}"

    def upload_url(self, container: str, path: str, ttl: int) -> str:
        """Link for ONE single-request PUT that can create the blob but never replace it.

        Permission is create only, on purpose. Verified live: a link that also has `write` can
        overwrite the blob, take a lease, and break any lease the service holds, so a file could
        be swapped after it was checked. With create only, Azure answers 403 to an overwrite,
        a lease or a block upload. The client must send x-ms-blob-type: BlockBlob and an
        x-ms-version of 2019-12-12 or later (single uploads up to 5000 MiB).
        """
        return self._signed_url(container, path, BlobSasPermissions(create=True), ttl)

    def download_url(self, container: str, path: str, ttl: int, filename: str | None = None) -> str:
        options = {"content_disposition": f'attachment; filename="{filename}"'} if filename else {}
        return self._signed_url(container, path, BlobSasPermissions(read=True), ttl, **options)

    def info(self, container: str, path: str) -> BlobInfo | None:
        try:
            props = self._client.get_blob_client(container, path).get_blob_properties()
        except ResourceNotFoundError:
            return None
        return BlobInfo(props.size, props.content_settings.content_type)

    def delete(self, container: str, path: str) -> None:
        try:
            self._client.get_blob_client(container, path).delete_blob()
        except ResourceNotFoundError:
            pass  # already gone


@lru_cache
def get_storage() -> BlobStorage:
    if not settings.AZURE_STORAGE_ACCOUNT_URL:
        raise StorageNotConfigured("AZURE_STORAGE_ACCOUNT_URL is not set")
    return BlobStorage(settings.AZURE_STORAGE_ACCOUNT_URL)
