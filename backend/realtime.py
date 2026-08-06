"""
realtime.py
-----------
Tiny in-process event bus that pushes live events (attendance punches,
membership renewals) to connected web clients over Server-Sent Events.

Why this exists: the attendance poller, the ADMS push handler and the pyzk
live transport all write punches in the background, and the frontend could
previously only learn about them by re-querying every few seconds. Here every
write publishes a broadcast and GET /api/realtime/stream (routers/realtime.py)
fans it out to open browser connections the instant it happens -- so a swipe
appears on screen with no polling delay. The 5-second react-query refetch
stays in place as a fallback if the stream ever drops.

Thread-safety: publish() is safe to call from any thread. Writers run in
different places (the asyncio event loop for the pyzk poller/live coroutines
and the ADMS handlers; FastAPI's worker threads for sync endpoints like the
manual check-in), so events funnel through loop.call_soon_threadsafe whenever
the caller is not on the loop.
"""

import asyncio
import itertools
import json
import logging
import threading

logger = logging.getLogger("studysync.realtime")

_subscriber_seq = itertools.count(1)
_subscribers_lock = threading.Lock()
_subscribers = {}  # id -> asyncio.Queue

_loop = None
_loop_lock = threading.Lock()


def bind(loop) -> None:
    """Pin the event loop the SSE streams live on. Called from lifespan."""
    global _loop
    with _loop_lock:
        _loop = loop


def _broadcast(event: str, data) -> None:
    payload = json.dumps(data, default=str)
    with _subscribers_lock:
        queues = list(_subscribers.values())
    for q in queues:
        try:
            q.put_nowait({"event": event, "data": payload})
        except asyncio.QueueFull:
            # Slowest client falls behind: drop its oldest item so it can
            # catch up instead of wedging the bus for everyone else.
            try:
                q.get_nowait()
                q.put_nowait({"event": event, "data": payload})
            except asyncio.QueueEmpty:
                pass


def publish(event: str, data=None) -> None:
    """Broadcast one event to every connected client. Thread-safe."""
    with _loop_lock:
        loop = _loop
    if loop is None or loop.is_closed():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        _broadcast(event, data)
    else:
        loop.call_soon_threadsafe(_broadcast, event, data)


def subscribe():
    """Register a subscriber. Returns (subscriber_id, asyncio.Queue)."""
    q = asyncio.Queue(maxsize=100)
    with _subscribers_lock:
        sid = next(_subscriber_seq)
        _subscribers[sid] = q
    return sid, q


def unsubscribe(sid) -> None:
    """Drop a subscriber (called when its SSE connection closes)."""
    with _subscribers_lock:
        _subscribers.pop(sid, None)


async def event_stream():
    """
    Async generator yielding SSE frames for one connected client.

    Emits ": keepalive" every 15s of silence so proxies and the browser
    keep the connection open, and cleans up its own subscription on exit.
    """
    sid, q = subscribe()
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"event: {item['event']}\ndata: {item['data']}\n\n"
    finally:
        unsubscribe(sid)
