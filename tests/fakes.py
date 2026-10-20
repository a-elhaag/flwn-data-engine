"""Test doubles shared by the file tests."""

from app.storage.blobs import BlobInfo


class FakeBlobs:
    """Stands in for BlobStorage: keeps blobs in memory and returns predictable links."""

    def __init__(self):
        self.stored: dict[tuple[str, str], BlobInfo] = {}
        self.data: dict[tuple[str, str], bytes] = {}
        self.deleted: list[tuple[str, str]] = []
        self.fail_deletes = False

    def upload_url(self, container, path, ttl):
        return f"https://fake.blob/{container}/{path}?sig=upload&ttl={ttl}"

    def download_url(self, container, path, ttl, filename=None):
        return f"https://fake.blob/{container}/{path}?sig=read&ttl={ttl}&name={filename}"

    def info(self, container, path):
        return self.stored.get((container, path))

    def download(self, container, path, max_bytes):
        from app.storage.errors import Unindexable

        info = self.stored[(container, path)]
        if info.size > max_bytes:
            raise Unindexable("too large to index")
        return self.data.get((container, path), b"")

    def delete(self, container, path):
        if self.fail_deletes:
            raise RuntimeError("storage is down")
        self.deleted.append((container, path))
        self.stored.pop((container, path), None)
        self.data.pop((container, path), None)

    def upload(self, ticket, size=None, content_type="application/octet-stream", data=None):
        """What the client does: PUT the bytes to the ticket's URL."""
        container, path = (
            ticket["upload_url"].split("https://fake.blob/")[1].split("?")[0].split("/", 1)
        )
        if (container, path) in self.stored:  # Azure: 403 UnauthorizedBlobOverwrite
            raise PermissionError("a create-only link cannot replace an existing blob")
        size = len(data) if data is not None else size
        self.stored[(container, path)] = BlobInfo(size, content_type)
        if data is not None:
            self.data[(container, path)] = data
        return container, path
