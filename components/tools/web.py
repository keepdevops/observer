"""Web-fetch tool: GET a URL and return (truncated) text. Args: {url, timeout?, max_chars?}."""
from __future__ import annotations

import aiohttp


async def run(args: dict) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        raise ValueError("web requires 'url'")
    timeout = aiohttp.ClientTimeout(total=float(args.get("timeout", 10)))
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        async with sess.get(url) as resp:
            resp.raise_for_status()
            text = await resp.text()
    return text[: int(args.get("max_chars", 2000))]
