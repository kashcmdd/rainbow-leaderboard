"""Small in-process rate limiter.

Enough for a single-container community app: one uvicorn process, no extra
dependencies. Keys combine the route with the signed-in user (or client IP),
so one busy member cannot drown the match-report endpoint.
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_buckets: dict[str, deque] = defaultdict(deque)


def _client_key(request: Request) -> str:
    user = request.session.get("user") if "session" in request.scope else None
    if user and user.get("id"):
        return f"user:{user['id']}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def rate_limit(limit: int, window_seconds: float):
    async def dependency(request: Request) -> None:
        key = f"{request.url.path}:{_client_key(request)}"
        now = time.monotonic()
        bucket = _buckets[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Wait a moment and try again.",
            )
        bucket.append(now)

        # Keep the dict from growing without bound.
        if len(_buckets) > 10000:
            for k in [k for k, v in _buckets.items() if not v]:
                _buckets.pop(k, None)

    return dependency
