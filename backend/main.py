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

import os
import socket
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import apply_runtime_schema_guards

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
)


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
    lan_ip = _detect_lan_ip()
    if lan_ip:
        print(f"\n  On this network, reach the API at: http://{lan_ip}:8000/docs")
        print(f"  (Point the frontend's API base URL at: http://{lan_ip}:8000)\n")
    yield


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
    r")(?::\d+)?$"
)

app = FastAPI(
    title="Library & Study Centre Management API",
    description="Backend API for student tracking, library usage, exams, and quizzes.",
    version="0.1.0",
    lifespan=lifespan,
)

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


@app.get("/", tags=["Health"])
def health_check():
    """Basic health check — confirms the server is reachable."""
    return {"status": "running", "message": "Library Management API is up"}
