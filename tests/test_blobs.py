"""The real BlobStorage client, offline: it must sign narrow, short, HTTPS-only links."""

import base64
import unittest
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import env  # noqa: F401  (sets the environment the app needs to import)
from azure.core.credentials import AzureNamedKeyCredential
from azure.storage.blob import UserDelegationKey

from app.storage.blobs import BlobStorage


def storage() -> BlobStorage:
    blobs = BlobStorage(
        "https://stflwndatauae01.blob.core.windows.net",
        credential=AzureNamedKeyCredential("stflwndatauae01", base64.b64encode(b"k" * 32).decode()),
    )
    key = UserDelegationKey()  # what Azure would return; fabricated so no network is needed
    key.signed_oid = key.signed_tid = "00000000-0000-0000-0000-000000000000"
    key.signed_service, key.signed_version = "b", "2023-11-03"
    key.signed_start = datetime.now(UTC) - timedelta(minutes=5)
    key.signed_expiry = datetime.now(UTC) + timedelta(hours=6)
    key.value = base64.b64encode(b"v" * 32).decode()
    blobs._key, blobs._key_expires = key, key.signed_expiry
    return blobs


def query(url: str) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(urlparse(url).query).items()}


class BlobStorageTest(unittest.TestCase):
    def test_upload_link_can_only_create_and_write_one_blob_over_https(self):
        url = storage().upload_url("chat-media", "ws/f1/note.opus", ttl=900)
        params = query(url)
        self.assertEqual(urlparse(url).path, "/chat-media/ws/f1/note.opus")
        self.assertEqual(params["sp"], "cw")  # create + write: no read, no delete, no list
        self.assertEqual(params["sr"], "b")  # one blob, not a container
        self.assertEqual(params["spr"], "https")
        expiry = datetime.fromisoformat(params["se"].replace("Z", "+00:00"))
        self.assertLessEqual(expiry - datetime.now(UTC), timedelta(seconds=905))

    def test_download_link_is_read_only_and_names_the_file(self):
        url = storage().download_url(
            "workspace-files", "ws/f1/spec.pdf", ttl=300, filename="spec.pdf"
        )
        params = query(url)
        self.assertEqual(params["sp"], "r")
        self.assertEqual(params["rscd"], 'attachment; filename="spec.pdf"')

    def test_the_delegation_key_is_reused_not_refetched_per_link(self):
        blobs = storage()
        first = blobs._delegation_key()
        blobs.upload_url("agent-reports", "a/b", 60)
        self.assertIs(blobs._delegation_key(), first)


if __name__ == "__main__":
    unittest.main()
