# StudySync — Front Desk (React + Vite + TypeScript)

A front-end for the StudySync FastAPI backend: student records, attendance,
digital & offline library usage, subscriptions, books catalog, exams, and
quizzes — built for the front-desk staff at a study centre.

## Stack

- React 19 + TypeScript + Vite
- React Router for navigation
- TanStack Query for data fetching, caching, and mutations
- Axios for HTTP
- Tailwind CSS v4 (custom "reading room" design tokens — see `src/index.css`)
- react-hot-toast for notifications
- lucide-react for icons

## Getting started

```bash
npm install
npm run dev
```

The app opens at `http://localhost:5173`.

## Connecting to the backend

Nothing is hard-coded — on first load you'll be sent to **Settings**, where
you enter:

- **API base URL** — where `uvicorn` is running, e.g. `http://localhost:8000`
  or `http://<lan-ip>:8000` if the backend is on another machine on the same
  network.
- **Staff API key** — the same value as the backend's `STUDYSYNC_API_KEY`
  environment variable. It's sent as the `X-API-Key` header on every request.

Use the **Test connection** button to confirm the key and URL both work
before relying on the rest of the app. These values live only in this
browser's local storage.

### Backend CORS

The backend's `STUDYSYNC_ALLOWED_ORIGINS` environment variable must include
wherever this frontend is served from. It already defaults to
`http://localhost:3000,http://localhost:5173`, which covers `npm run dev`
and `npm run preview` out of the box.

## Project layout

```
src/
  api/            Axios client + one file per backend resource (typed
                   request/response shapes + React Query hooks)
  components/     Shared layout and UI primitives (Button, Modal, Table,
                   the "card-catalog tab" ID/status badges, StudentPicker…)
  context/        Settings (base URL + API key) persisted to localStorage
  lib/            Formatting helpers, debounce hook
  pages/          One folder per section (students, attendance,
                   digital-library, offline-library, books, subscriptions,
                   exams, quizzes, settings)
  router.tsx      Route table
```

Every type in `src/api/types.ts` mirrors a Pydantic model in
`backend/models/*.py` field-for-field, and every hook in `src/api/*.ts` maps
to the matching FastAPI router, including the check-in/check-out workflows
for attendance and digital library sessions, the nested marks/scores
endpoints for exams and quizzes, and the list filters each `GET` endpoint
supports.

## Build

```bash
npm run build   # type-checks with tsc -b, then builds to dist/
npm run preview # serve the production build locally
```
