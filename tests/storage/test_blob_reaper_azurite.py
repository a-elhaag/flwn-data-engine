"""The reaper against a real blob service (Azurite, the local Azure Storage emulator).

Skipped unless AZURITE_URL is set, for example http://127.0.0.1:10000/devstoreaccount1. Run it
with `docker run -p 127.0.0.1:10000:10000 mcr.microsoft.com/azure-storage/azurite azurite-blob
--blobHost 0.0.0.0`. It never touches a real storage account.
"""

import os
import unittest
import uuid

import db_support
from sqlalchemy import delete, select

from app.db.models.files import BlobPurge
from app.db.session import workspace_session
from app.domain.storage.blob_reaper import reap_blob_purges
from app.domain.storage.blobs import BlobStorage

AZURITE_URL = os.environ.get("AZURITE_URL", "")
# Azurite's published development key. It is not a secret.
AZURITE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)


@unittest.skipUnless(AZURITE_URL, "set AZURITE_URL to run against Azurite")
class BlobReaperAzuriteTest(unittest.TestCase):
    def setUp(self):
        from azure.core.credentials import AzureNamedKeyCredential
        from azure.storage.blob import BlobServiceClient

        self.container = f"reap-{uuid.uuid4().hex[:8]}"
        self.service = BlobServiceClient(
            AZURITE_URL,
            credential=AzureNamedKeyCredential("devstoreaccount1", AZURITE_KEY),
        )
        self.service.create_container(self.container)
        self.storage = BlobStorage(
            AZURITE_URL, credential=AzureNamedKeyCredential("devstoreaccount1", AZURITE_KEY)
        )
        self.engine = db_support.engine()
        self.factory = lambda: workspace_session(self.engine, "", service=True)
        with self.factory() as session:
            session.execute(delete(BlobPurge))

    def upload(self, path):
        self.service.get_blob_client(self.container, path).upload_blob(b"bytes")

    def exists(self, path):
        return self.service.get_blob_client(self.container, path).exists()

    def queue(self, path):
        with self.factory() as session:
            session.add(
                BlobPurge(workspace_id=uuid.uuid4(), container=self.container, blob_path=path)
            )

    def test_reaper_deletes_the_real_blob_and_its_row(self):
        path = "ws/docs/report.pdf"
        self.upload(path)
        self.queue(path)
        counts = reap_blob_purges(self.factory, self.storage)
        self.assertEqual(counts, {"deleted": 1, "missing": 0, "failed": 0})
        self.assertFalse(self.exists(path))
        with self.factory() as session:
            self.assertEqual(session.scalars(select(BlobPurge.id)).all(), [])

    def test_a_blob_already_gone_is_cleared_without_error(self):
        self.queue("ws/never-uploaded.pdf")
        counts = reap_blob_purges(self.factory, self.storage)
        self.assertEqual(counts, {"deleted": 0, "missing": 1, "failed": 0})
        with self.factory() as session:
            self.assertEqual(session.scalars(select(BlobPurge.id)).all(), [])

    def tearDown(self):
        self.service.delete_container(self.container)
