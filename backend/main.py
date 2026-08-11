"""
main.py
-------
FastAPI application entrypoint.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

--host 0.0.0.0 is what makes this reachable from other computers on the
same network — binding to 127.0.0.1 (the default) would restrict access
to only this machine.

Once running, open http://<this-machine's-LAN-IP>:8000/docs from ANY
computer on the same network to see the interactive API documentation
and test every endpoint directly from the browser. The LAN IP is also
printed to the console on startup below, so you don't have to look it
up yourself.
"""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import contextlib
import logging
import os
import socket
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from database import apply_runtime_schema_guards
from rate_limit import limiter

logger = logging.getLogger("studysync")

from routers import (
    students,
    attendance,
    digital_library,
    subscriptions,
    offline_library,
    books,
    exams,
    quizzes,
    dashboard,
    coaching,
    other_activities,
    realtime,
    sync,
)

# --- ZKTeco attendance integration (PyZK only) ---
#
# zkteco/ (package) + routers/zkteco.py -- OUR server connects OUT to the
# device over pyzk's TCP client and either polls its buffer
# (zkteco/poller.py), holds a live_capture() connection open
# (zkteco/live.py), or runs the periodic full-buffer reconciliation
# backstop (zkteco/reconcile.py). Needs ZK_DEVICE_IP set and the `pyzk`
# package installed.
#
# ZK_INTEGRATION selects how much this process wires up:
#   "pyzk" (default) -- mount the PyZK poll/live/reconcile path.
#   "none" -- no device integration at all (e.g. testing the rest of the
#             app with no device configured).
# The legacy values "both" and "adms" are accepted for backward
# compatibility and both mean PyZK, since the old ADMS push transport has
# been removed.
ZK_INTEGRATION = os.environ.get("ZK_INTEGRATION", "pyzk").strip().lower()
if ZK_INTEGRATION not in ("pyzk", "adms", "both", "none"):
    logger.warning(
        "Unrecognised ZK_INTEGRATION=%r, falling back to 'pyzk'.", ZK_INTEGRATION
    )
    ZK_INTEGRATION = "pyzk"

pyzk_enabled = ZK_INTEGRATION != "none"

zkteco = None
zkteco_poller_loop = None
zkteco_live_loop = None
attendance_mode = None
if pyzk_enabled:
    try:
        from routers import zkteco as zkteco  # noqa: F811 (intentional re-import)
        from zkteco.poller import zkteco_poller_loop
        from zkteco.live import zkteco_live_loop
        from zkteco.reconcile import zkteco_reconcile_loop
        from zkteco.config import attendance_mode
    except ImportError as e:
        logger.warning(
            "PyZK ZKTeco integration unavailable (%s) -- skipping it. "
            "Set ZK_INTEGRATION=none to silence this if that's intentional.",
            e,
        )
        pyzk_enabled = False


def _detect_lan_ip() -> Optional[str]:
    """Best-effort LAN IP for the startup banner. Never raises."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    apply_runtime_schema_guards()
    # SSE streams for live attendance/renewal events must be created and
    # drained on the same loop the app runs on; pin it here so publish()
    # (which can be called from worker threads) reaches them safely.
    from realtime import bind

    bind(asyncio.get_running_loop())
    lan_ip = _detect_lan_ip()
    if lan_ip:
        print(f"\n  On this network, reach the API at: http://{lan_ip}:8000/docs")
        print(f"  (Point the frontend's API base URL at: http://{lan_ip}:8000)\n")

    # Only the pyzk path needs a background task -- it's the one that
    # connects OUT to the device (either polling or holding a live
    # connection open). zkteco_poller_loop/zkteco_live_loop also
    # self-disable if ZK_DEVICE_IP isn't set, so this is a second layer of
    # "off by default until configured", not the only one.
    stop_event = asyncio.Event()
    zkteco_tasks = []
    if pyzk_enabled:
        mode = attendance_mode()
        # mode "poll" (default): periodic buffer pulls. mode "live":
        # realtime punch stream. mode "both": the live stream PLUS a
        # periodic pull as a safety net. StudySync never clears the
        # device buffer -- it is a pure reader. If the device refuses the
        # poll's second concurrent session, drop back to
        # ZK_ATTENDANCE_MODE=live -- the reconcile loop below stays
        # active as the completeness backstop either way.
        if mode in ("live", "both"):
            zkteco_tasks.append(asyncio.create_task(zkteco_live_loop(stop_event)))
        if mode in ("poll", "both"):
            zkteco_tasks.append(asyncio.create_task(zkteco_poller_loop(stop_event)))
    # The reconciliation backstop runs whenever pyzk is available (device
    # configured), alongside whichever attendance transport is selected. It
    # is the completeness guarantee: it re-reads the full device buffer and
    # captures anything that slipped past poll/live, and persists device
    # sync health.
    reconcile_task = None
    if pyzk_enabled:
        reconcile_task = asyncio.create_task(zkteco_reconcile_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        for task in zkteco_tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if reconcile_task is not None:
            reconcile_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reconcile_task


# Always allow the local dev servers on this machine.
DEFAULT_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
]

# STUDYSYNC_ALLOWED_ORIGINS: comma-separated list of extra exact origins to
# allow, e.g. "https://studysync.example.com,http://10.0.0.5:5173" for a
# frontend that isn't on your local network. This was documented but never
# actually wired up before — now it is.
_env_origins = [
    origin.strip()
    for origin in os.environ.get("STUDYSYNC_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
allowed_origins = DEFAULT_ORIGINS + _env_origins

# For everyday LAN testing (phone/tablet/another laptop on the same wifi),
# hardcoding one IP breaks the moment it changes. This regex instead allows
# any device on a private network (RFC 1918: 10.x, 172.16-31.x, 192.168.x)
# on any port, so "open the frontend on my phone" just works without
# editing this file or setting an env var every time your IP changes.
LAN_ORIGIN_REGEX = (
    r"^http://("
    r"localhost"
    r"|127\.0\.0\.1"
    r"|10(?:\.\d{1,3}){3}"
    r"|172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r")(?::(?:5173|3000))?$"
)

app = FastAPI(
    title="Library & Study Centre Management API",
    description="Backend API for student tracking, library usage, exams, and quizzes.",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Rate limiting ---
# Global limiter keyed by real client IP (first X-Forwarded-For hop from
# Caddy; see rate_limit.py). 120 req/min default for general endpoints,
# tighter limits applied per-endpoint where needed (sync, PDF gen).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# The middleware is what applies the default limit to every route; routes
# decorated with @limiter.limit(...) override the default for that route.
app.add_middleware(SlowAPIMiddleware)

# CORS: explicit origins above (localhost + anything from
# STUDYSYNC_ALLOWED_ORIGINS) plus the private-LAN regex for local network
# testing. Set allow_credentials=True only if you start sending cookies —
# it's invalid combined with a wildcard/regex origin match otherwise.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=LAN_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Security headers ---
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Mount routers. Each new module (attendance, books, exams, etc.) gets
# added here as it's built.
app.include_router(students.router)
app.include_router(attendance.router)
app.include_router(digital_library.router)
app.include_router(subscriptions.router)
app.include_router(offline_library.router)
app.include_router(books.router)
app.include_router(exams.router)
app.include_router(exams.marks_router)
app.include_router(quizzes.router)
app.include_router(quizzes.scores_router)
app.include_router(dashboard.router)
app.include_router(coaching.router)
app.include_router(other_activities.router)
app.include_router(realtime.router)
app.include_router(sync.router)
if pyzk_enabled and zkteco is not None:
    app.include_router(zkteco.router)


@app.get("/", tags=["Health"])
def health_check():
    """Basic health check — confirms the server is reachable."""
    return {"status": "running", "message": "Library Management API is up"}
