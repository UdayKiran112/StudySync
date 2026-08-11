"""
routers/realtime.py
--------------------
Server-Sent Events endpoint that keeps the frontend in lockstep with
background attendance writes.

The pyzk poller, the pyzk live transport and the manual front-desk flow all
import apply_punch() (attendance_punch.py), which publishes an "attendance"
event every time it opens or closes a session. The membership gateway
auto_renew_if_expired() (routers/students.py) publishes a "renewal" event
when a lapsed membership is reactivated by a check-in. This router turns
those broadcasts into a `text/event-stream` response the browser consumes,
so a swipe shows up on the Attendance page the instant it is written.

Events:
    attendance  {"student_id", "name", "day", "punch", "outcome"}  -- a punch applied
    renewal     {"student_id", "name"}                     -- membership auto-renewed

The connection is authenticated with the same X-API-Key header as every
other staff endpoint; the key is checked once when the connection opens.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from realtime import event_stream
from security import require_api_key

router = APIRouter(
    prefix="/api/realtime",
    tags=["Realtime"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/stream")
async def stream():
    """Keep an SSE connection open and stream live attendance/renewal events."""
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
