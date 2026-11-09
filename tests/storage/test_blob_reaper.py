"""The reaper deletes blobs recorded in blob_purges, then their rows, and retries what failed."""

import unittest
import uuid

import db_support
from fakes import FakeBlobs
from sqlalchemy import delete, select

from app.db.models.files import BlobPurge
from app.db.session import workspace_session
from app.domain.storage.blob_reaper import reap_blob_purges
from app.domain.storage.blobs import BlobInfo


class FlakyBlobs(FakeBlobs):
    """Fails the delete of any path in fail_paths; the rest behave like FakeBlobs."""

    def __init__(self):
        super().__init__()
        self.fail_paths: set[str] = set()

    def delete(self, container, path):
        if path in self.fail_paths:
            self.deleted.append((container, path))
            raise RuntimeError("storage is down for this blob")
        return super().delete(container, path)


class BlobReaperTest(unittest.TestCase):
    def setUp(self):
        self.engine = db_support.engine()
        self.factory = lambda: workspace_session(self.engine, "", service=True)
        with self.factory() as session:
            session.execute(delete(BlobPurge))
        self.blobs = FlakyBlobs()

    def add_purge(self, path, stored=True):
        """One blob_purges row, in its own transaction so created_at increases with each call."""
        with self.factory() as session:
            session.add(
                BlobPurge(workspace_id=uuid.uuid4(), container="workspace-files", blob_path=path)
            )
        if stored:
            self.blobs.stored[("workspace-files", path)] = BlobInfo(size=1, content_type=None)
        return path

    def remaining(self):
        with self.factory() as session:
            return sorted(session.scalars(select(BlobPurge.blob_path)).all())

    def test_successful_delete_removes_blob_and_row(self):
        self.add_purge("a.pdf")
        counts = reap_blob_purges(self.factory, self.blobs)
        self.assertEqual(counts, {"deleted": 1, "missing": 0, "failed": 0})
        self.assertEqual(self.blobs.deleted, [("workspace-files", "a.pdf")])
        self.assertEqual(self.remaining(), [])

    def test_missing_blob_still_removes_row(self):
        self.add_purge("gone.pdf", stored=False)
        counts = reap_blob_purges(self.factory, self.blobs)
        self.assertEqual(counts, {"deleted": 0, "missing": 1, "failed": 0})
        self.assertEqual(self.remaining(), [])

    def test_failing_blob_stays_queued_and_batch_continues(self):
        for path in ("one.pdf", "two.pdf", "three.pdf"):
            self.add_purge(path)
        self.blobs.fail_paths = {"two.pdf"}
        counts = reap_blob_purges(self.factory, self.blobs)
        self.assertEqual(counts, {"deleted": 2, "missing": 0, "failed": 1})
        self.assertEqual(self.remaining(), ["two.pdf"])

        self.blobs.fail_paths = set()  # storage recovers: the next run retries it
        self.assertEqual(
            reap_blob_purges(self.factory, self.blobs),
            {"deleted": 1, "missing": 0, "failed": 0},
        )
        self.assertEqual(self.remaining(), [])

    def test_second_run_does_not_delete_twice(self):
        for path in ("a.pdf", "b.pdf", "c.pdf"):
            self.add_purge(path)
        reap_blob_purges(self.factory, self.blobs)
        again = reap_blob_purges(self.factory, self.blobs)
        self.assertEqual(again, {"deleted": 0, "missing": 0, "failed": 0})
        self.assertEqual(len(self.blobs.deleted), 3)

    def test_row_held_by_another_replica_is_skipped(self):
        self.add_purge("locked.pdf")
        self.add_purge("free.pdf")
        with self.engine.connect() as other:  # a second replica mid-reap holds the first row
            tx = other.begin()
            other.execute(
                select(BlobPurge.id).where(BlobPurge.blob_path == "locked.pdf").with_for_update()
            )
            counts = reap_blob_purges(self.factory, self.blobs)
            tx.rollback()
        self.assertEqual(counts, {"deleted": 1, "missing": 0, "failed": 0})
        self.assertEqual(self.remaining(), ["locked.pdf"])

    def test_batch_size_is_respected_and_oldest_goes_first(self):
        paths = [self.add_purge(f"{i}.pdf") for i in range(5)]
        counts = reap_blob_purges(self.factory, self.blobs, batch_size=2)
        self.assertEqual(counts["deleted"], 2)
        self.assertEqual([p for _, p in self.blobs.deleted], paths[:2])
        self.assertEqual(len(self.remaining()), 3)


if __name__ == "__main__":
    unittest.main()
