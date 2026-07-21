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

## Demo records

To populate every data module with 30 realistic sample records (including
30 students), run the following from the project root. The script is
idempotent: rerunning it adds only missing `DEMO` records and does not delete
your existing data.

```powershell
& .\study_sync\Scripts\python.exe .\backend\seed_demo_data.py
```

## API tests

The backend integration suite covers students, books, subscriptions,
attendance, digital and offline library usage, exams and marks, quizzes and
scores, and the dashboard. It runs against a temporary database.

```powershell
& .\study_sync\Scripts\python.exe -m unittest discover -s backend/tests -v
```
A modern Windows desktop application for managing study centers and libraries with student attendance, digital &amp; offline library usage, exams, quizzes, reports, and Google Sheets synchronization. Built using Python, PySide6, and SQLite.
