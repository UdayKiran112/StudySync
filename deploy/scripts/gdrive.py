"""
gdrive.py
---------
Shared Google Drive helpers for the StudySync ops scripts (backup.py,
healthcheck.py). Uploads / downloads / prunes the nightly database backup
zips to a Google Drive folder, using the SAME service account JSON as the
Google Sheets sync (GOOGLE_CREDS_FILE).

Config comes from C:/ProgramData/StudySync/app/api/.env:
  GOOGLE_CREDS_FILE            - path to the service-account JSON key
  GOOGLE_DRIVE_FOLDER_ID       - Drive folder shared with the service account
  STUDYSYNC_BACKUP_RETENTION_DAYS - how many days of backups to keep remotely

The Task Scheduler runs these exes without the API's environment loaded, so
each script calls load_env() first to read the values from the .env file.

Only the least-privilege scope "drive.file" is requested: the service account
can touch exactly the files it created in the shared folder and nothing else.
"""

import datetime as dt
import json
import os
from pathlib import Path
from urllib.parse import quote

APP_DIR = Path(os.getenv("STUDYSYNC_APP_DIR", r"C:\ProgramData\StudySync"))
ENV_FILE = APP_DIR / "app" / "api" / ".env"

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_MIME_ZIP = "application/zip"


def load_env() -> None:
    """Merge APP_DIR/app/api/.env into os.environ (never overrides values the
    caller already set). Lets the ops exes see GOOGLE_* / STUDYSYNC_* config
    even when launched by Task Scheduler without a loaded environment."""
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def _creds_path() -> Path | None:
    raw = os.environ.get("GOOGLE_CREDS_FILE", "").strip()
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p
    # Match the backend: a relative GOOGLE_CREDS_FILE is resolved from the
    # api directory the service runs in.
    candidate = APP_DIR / "app" / "api" / p
    return candidate if candidate.exists() else p


def drive_folder_id() -> str:
    return os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()


def enabled() -> bool:
    """True when a creds file exists AND a Drive folder id is configured."""
    if not drive_folder_id():
        return False
    path = _creds_path()
    return path is not None and path.exists()


def get_session():
    """Build an authorized HTTP session for the service account."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials

    path = _creds_path()
    if path is None or not path.exists():
        raise FileNotFoundError(f"GOOGLE_CREDS_FILE not found: {path}")
    creds = Credentials.from_service_account_file(str(path), scopes=_SCOPES)
    return AuthorizedSession(creds)


def list_remote(session) -> list[dict]:
    """Return the studysync_*.zip files in the folder, newest first."""
    folder = drive_folder_id()
    q = f"'{folder}' in parents and trashed=false and name contains 'studysync_'"
    url = (
        f"{_FILES_URL}?q={quote(q)}"
        "&fields=files(id,name,createdTime,size)"
        "&orderBy=createdTime desc&pageSize=1000&spaces=drive"
    )
    r = session.get(url)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Drive list failed ({r.status_code}): {r.text[:300]}")
    files = r.json().get("files", [])
    return [f for f in files if f["name"].startswith("studysync_") and f["name"].endswith(".zip")]


def upload_file(session, path: Path) -> None:
    """Upload one backup zip via the resumable protocol (any size works)."""
    folder = drive_folder_id()
    meta = json.dumps({"name": path.name, "parents": [folder], "mimeType": _MIME_ZIP}).encode()
    r = session.post(
        f"{_UPLOAD_URL}?uploadType=resumable",
        data=meta,
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": _MIME_ZIP,
        },
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Drive session create failed ({r.status_code}): {r.text[:300]}")
    location = r.headers.get("Location")
    if not location:
        raise RuntimeError("Drive upload: no resumable session Location header")
    with open(path, "rb") as fh:
        body = fh.read()
    up = session.put(location, data=body, headers={"Content-Type": _MIME_ZIP})
    if up.status_code not in (200, 201):
        raise RuntimeError(f"Drive upload failed ({up.status_code}): {up.text[:300]}")


def download_file(session, file_id: str, dest: Path) -> None:
    """Download a remote backup to dest (streamed)."""
    r = session.get(f"{_FILES_URL}/{file_id}?alt=media", stream=True)
    if r.status_code != 200:
        raise RuntimeError(f"Drive download failed ({r.status_code}): {r.text[:300]}")
    with open(dest, "wb") as fh:
        for chunk in r.iter_content(chunk_size=1 << 20):
            fh.write(chunk)


def delete_file(session, file_id: str) -> None:
    r = session.delete(f"{_FILES_URL}/{file_id}")
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Drive delete failed ({r.status_code}): {r.text[:300]}")


def prune_remote(session, retention_days: int) -> int:
    """Delete remote backups older than retention_days. Returns count removed."""
    if retention_days <= 0:
        return 0
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention_days)
    removed = 0
    for f in list_remote(session):
        created = f.get("createdTime", "")
        try:
            created_dt = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created_dt < cutoff:
            delete_file(session, f["id"])
            removed += 1
    return removed


def download_newest(session, dest_dir: Path) -> Path | None:
    """Download the newest remote backup into dest_dir. Returns the path or
    None when the folder is empty / not configured."""
    files = list_remote(session)
    if not files:
        return None
    newest = files[0]
    dest = dest_dir / f"remote_{newest['name']}"
    download_file(session, newest["id"], dest)
    return dest
