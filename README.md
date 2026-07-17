# StudySync

## Backend configuration

Set a staff API key before running the backend. Every `/api/*` endpoint
requires it in the `X-API-Key` request header (Swagger UI supports this via
its **Authorize** button).

```powershell
$env:STUDYSYNC_API_KEY = "replace-with-a-long-random-secret"
```

Optionally configure the allowed frontend origins:

```powershell
$env:STUDYSYNC_ALLOWED_ORIGINS = "http://localhost:5173,http://localhost:3000"
```
A modern Windows desktop application for managing study centers and libraries with student attendance, digital &amp; offline library usage, exams, quizzes, reports, and Google Sheets synchronization. Built using Python, PySide6, and SQLite.
