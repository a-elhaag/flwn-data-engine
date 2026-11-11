"""Rate-limit counters: count, hit and reset per (bucket, key hash). Service key only.

The backend hashes the key and decides what the limit is; this module only counts.
Plain functions over a session; the route opens it with service_session().
"""

from datetime import timedelta

from sqlalchemy import delete, func, select, text

from app.db.models.auth import RateHit

KEEP = timedelta(days=1)  # hits older than this are deleted on the next hit


def _live(bucket: str, key_hash: str, window_seconds: int):
    return (
        RateHit.bucket == bucket,
        RateHit.key_hash == key_hash,
        RateHit.hit_at > func.now() - timedelta(seconds=window_seconds),
    )


def count(session, bucket: str, key_hash: str, window_seconds: int) -> int:
    return session.scalar(
        select(func.count()).select_from(RateHit).where(*_live(bucket, key_hash, window_seconds))
    )


def hit(session, bucket: str, key_hash: str, window_seconds: int) -> int:
    # One hit at a time per key, held until commit, so parallel hits see each other and the
    # returned count is exact (the caller compares it with its limit).
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"rate:{bucket}:{key_hash}"},
    )
    session.execute(
        delete(RateHit).where(RateHit.bucket == bucket, RateHit.hit_at < func.now() - KEEP)
    )
    session.add(RateHit(bucket=bucket, key_hash=key_hash))
    session.flush()
    return count(session, bucket, key_hash, window_seconds)


def reset(session, bucket: str, key_hash: str) -> None:
    session.execute(delete(RateHit).where(RateHit.bucket == bucket, RateHit.key_hash == key_hash))
