"""Delete the blobs that a workspace delete recorded in blob_purges.

A workspace delete removes its rows in one transaction and cannot call Azure there, so it writes
one blob_purges row per blob. This reaper deletes each blob, then its row. The row is removed only
after the blob is gone (or was already missing); a failure leaves the row to be retried next run.

Run it with `bash run.sh reap` on a schedule (see README). Several replicas can run it at once:
each row is locked (FOR UPDATE SKIP LOCKED) while its blob is deleted.
"""

import logging
from collections import Counter

from sqlalchemy import select

from app.db import session as db
from app.db.models.files import BlobPurge
from app.domain.storage.blobs import get_storage

logger = logging.getLogger(__name__)


def reap_blob_purges(session_factory, storage, batch_size: int = 100) -> dict[str, int]:
    """Delete up to batch_size recorded blobs, oldest first.

    session_factory is a zero-argument callable returning a service-mode session context manager.
    Returns {"deleted", "missing", "failed"} counts. Rows another replica holds are skipped.
    """
    counts = {"deleted": 0, "missing": 0, "failed": 0}
    with session_factory() as session:
        ids = session.scalars(
            select(BlobPurge.id).order_by(BlobPurge.created_at, BlobPurge.id).limit(batch_size)
        ).all()
    for purge_id in ids:
        try:
            outcome = _reap_one(session_factory, storage, purge_id)
        except Exception:
            logger.exception("blob purge %s failed; it stays queued", purge_id)
            outcome = "failed"
        if outcome != "skipped":
            counts[outcome] += 1
    return counts


def _reap_one(session_factory, storage, purge_id) -> str:
    # One short transaction per row. The row lock is held while its blob is deleted, so no other
    # replica reaps it too. If the delete raises, the transaction rolls back and the row stays.
    with session_factory() as session:
        row = session.scalars(
            select(BlobPurge).where(BlobPurge.id == purge_id).with_for_update(skip_locked=True)
        ).one_or_none()
        if row is None:
            return "skipped"  # another replica has it, or it was already reaped
        existed = storage.delete(row.container, row.blob_path)
        session.delete(row)
    return "deleted" if existed else "missing"


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    storage = get_storage()
    totals: Counter[str] = Counter()
    while True:
        counts = reap_blob_purges(db.service_session, storage)
        totals.update(counts)
        # Stop when the queue is empty, or a whole batch failed (no progress). A poison row is
        # retried next run; it cannot loop this one forever.
        if not any(counts.values()) or counts["deleted"] + counts["missing"] == 0:
            break
    print(
        f"blob purge: deleted={totals['deleted']} missing={totals['missing']} "
        f"failed={totals['failed']}"
    )


if __name__ == "__main__":
    main()
