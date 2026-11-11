"""Rate-limit counters (service key only). The backend decides the limits and calls these."""

from fastapi import APIRouter, Depends, Response

from app.api.auth import service_only
from app.api.schemas.rate_limits import RateCount, RateKey, RateWindow
from app.db.session import service_session
from app.domain.identity import rate_limits as store

router = APIRouter(prefix="/rate-limits", dependencies=[Depends(service_only)])


def _run(fn, *args):
    with service_session() as session:
        return fn(session, *args)


@router.post("/count", response_model=RateCount)
def count_hits(body: RateWindow):
    return RateCount(count=_run(store.count, body.bucket, body.key_hash, body.window_seconds))


@router.post("/hit", response_model=RateCount)
def record_hit(body: RateWindow):
    return RateCount(count=_run(store.hit, body.bucket, body.key_hash, body.window_seconds))


@router.post("/reset", status_code=204)
def reset_hits(body: RateKey):
    _run(store.reset, body.bucket, body.key_hash)
    return Response(status_code=204)
