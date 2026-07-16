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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import students, attendance

app = FastAPI(
    title="Library & Study Centre Management API",
    description="Backend API for student tracking, library usage, exams, and quizzes.",
    version="0.1.0",
)

# CORS: allows the React frontend (running on a different port, e.g. 3000
# or 5173) to call this API. Since this is a private LAN app with just two
# staff, allowing all origins is fine — no public internet exposure.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers. Each new module (attendance, books, exams, etc.) gets
# added here as it's built.
app.include_router(students.router)
app.include_router(attendance.router)


@app.get("/", tags=["Health"])
def health_check():
    """Basic health check — confirms the server is reachable."""
    return {"status": "running", "message": "Library Management API is up"}
