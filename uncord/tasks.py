#background tasks keep a strong ref here so the loop cant drop them halfway, and failures land in the log
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("uncord")
_live: set = set()


def spawn(coro) -> asyncio.Future:
    t = asyncio.ensure_future(coro)
    _live.add(t)
    t.add_done_callback(_done)
    return t


def _done(t: asyncio.Future) -> None:
    _live.discard(t)
    if not t.cancelled() and t.exception() is not None:
        log.error("background task failed", exc_info=t.exception())


#on quit so nothing is left pending when the loop closes
async def cancelAll(timeout: float = 1.0) -> None:
    pending = [t for t in _live if not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.wait(pending, timeout=timeout)
