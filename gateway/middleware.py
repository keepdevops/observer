"""Gateway middleware: the design's Chain of Responsibility at the single external surface.

Order: **auth → rate-limit → logging → router** (aiohttp's own routing is the final 'router'
stage). Each stage processes the request and passes it on. Auth and rate-limit are off by
default (local dev) and enabled via config; internal bus components trust the broker and have
no such chain — this belongs only at the gateway boundary.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from aiohttp import web

logger = logging.getLogger(__name__)


def auth_middleware(api_key: str):
    """Reject requests without the API key (Bearer or X-API-Key). Empty key = disabled."""
    @web.middleware
    async def mw(request: web.Request, handler):
        if api_key:
            bearer = request.headers.get("Authorization", "")
            provided = bearer[7:].strip() if bearer.startswith("Bearer ") else ""
            provided = provided or request.headers.get("X-API-Key", "")
            if provided != api_key:
                logger.warning("auth: rejected %s %s", request.method, request.path)
                return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)
    return mw


def rate_limit_middleware(limit: int, window: float = 1.0):
    """Per-client token bucket: at most `limit` requests per `window` seconds. 0 = disabled."""
    hits: dict[str, deque] = defaultdict(deque)

    @web.middleware
    async def mw(request: web.Request, handler):
        if limit > 0:
            now = time.monotonic()
            dq = hits[request.remote or "?"]
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(dq) >= limit:
                logger.warning("rate-limit: 429 for %s", request.remote)
                return web.json_response({"error": "rate limited"}, status=429)
            dq.append(now)
        return await handler(request)
    return mw


@web.middleware
async def logging_middleware(request: web.Request, handler):
    start = time.monotonic()
    try:
        resp = await handler(request)
    except web.HTTPException as exc:
        logger.info("%s %s -> %d", request.method, request.path, exc.status)
        raise
    logger.info("%s %s -> %d (%.1fms)", request.method, request.path, resp.status,
                (time.monotonic() - start) * 1000)
    return resp


def build_chain(api_key: str = "", rate_limit: int = 0) -> list:
    """The ordered chain: auth → rate-limit → logging (router is aiohttp's own dispatch)."""
    return [auth_middleware(api_key), rate_limit_middleware(rate_limit), logging_middleware]
