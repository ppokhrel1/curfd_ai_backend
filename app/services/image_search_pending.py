"""
Lightweight in-memory pending registry for image search selections.
Allows pausing image-to-3D flow to wait for user to pick from candidates.
"""

import asyncio

_pending: dict[str, asyncio.Future] = {}


def register(request_id: str) -> asyncio.Future:
    """Register a new pending image selection request."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    fut = loop.create_future()
    _pending[request_id] = fut
    return fut


def resolve(request_id: str, selected_url: str) -> bool:
    """Resolve a pending image selection with the selected URL."""
    fut = _pending.pop(request_id, None)
    if fut and not fut.done():
        fut.set_result(selected_url)
        return True
    return False


def cancel(request_id: str) -> bool:
    """Cancel a pending image selection."""
    fut = _pending.pop(request_id, None)
    if fut and not fut.done():
        fut.cancel()
        return True
    return False


def cleanup_all():
    """Cancel all pending futures (for testing/cleanup)."""
    for fut in list(_pending.values()):
        if not fut.done():
            fut.cancel()
    _pending.clear()
