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
and test every endpoint directly from the browser.
"""

import os
from contextlib import asynccontextmanager

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
)

@asynccontextmanager
async def lifespan(_: FastAPI):
    apply_runtime_schema_guards()
    yield


allowed_origins = [
    origin.strip()
    for origin in os.getenv("STUDYSYNC_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
    if origin.strip()
]

app = FastAPI(
    title="Library & Study Centre Management API",
    description="Backend API for student tracking, library usage, exams, and quizzes.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS is restricted to local frontend origins by default. Set
# STUDYSYNC_ALLOWED_ORIGINS to a comma-separated list for a deployed frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
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


@app.get("/", tags=["Health"])
def health_check():
    """Basic health check — confirms the server is reachable."""
    return {"status": "running", "message": "Library Management API is up"}
