"""
sheets_client.py
----------------
Thin wrapper around gspread for full-rewrite sheet sync.

Configured via environment variables:
    GOOGLE_SPREADSHEET_ID  – the target Google Sheet's ID (required)
    GOOGLE_CREDS_FILE      – path to a service-account JSON key (default: credentials.json)
    STUDYSYNC_SHEETS_MAX_CELLS_PER_REQUEST
                           – max cells written in a single API call
                             (default: 100000). Google caps a single write
                             at ~500k cells and ~10 MB of payload, so large
                             tabs are automatically split into several
                             chunked writes to stay safely under both.

The Google Sheet must be shared with the service-account email
(found inside the JSON key file) with Editor access.
"""

import logging
import os
import time
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger("studysync.sheets")

_DEFAULT_MAX_CELLS_PER_REQUEST = 100_000

# Credentials are loaded once and cached.  Google service-account tokens
# are valid for ~1 hour; we re-read the JSON key file at most once per 30
# minutes so a rotated key takes effect without a restart, while avoiding
# 8 redundant file parses per full-sync cycle.
_CREDS_CACHE_TTL_SECONDS = 1800
_cached_creds: Optional[Credentials] = None
_cached_spreadsheet_id: str = ""
_cached_creds_loaded_at: float = 0.0


class SheetsConfigError(Exception):
    """Raised when Google Sheets configuration is missing or invalid."""


_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_credentials() -> tuple[Credentials, str]:
    """Load credentials (cached) and return (creds, spreadsheet_id).

    The JSON key file is read at most once every 30 minutes.  Between
    refreshes the cached object is returned directly, eliminating 7
    redundant file parses per 8-tab sync cycle.
    """
    global _cached_creds, _cached_spreadsheet_id, _cached_creds_loaded_at

    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "")
    if not spreadsheet_id:
        raise SheetsConfigError(
            "GOOGLE_SPREADSHEET_ID environment variable is not set."
        )

    now = time.monotonic()
    if (
        _cached_creds is not None
        and _cached_spreadsheet_id == spreadsheet_id
        and (now - _cached_creds_loaded_at) < _CREDS_CACHE_TTL_SECONDS
    ):
        return _cached_creds, spreadsheet_id

    creds_file = os.environ.get("GOOGLE_CREDS_FILE", "credentials.json")
    if not os.path.isfile(creds_file):
        raise SheetsConfigError(
            f"Service-account key not found: {creds_file}. "
            "Set GOOGLE_CREDS_FILE or place credentials.json in the working directory."
        )

    creds = Credentials.from_service_account_file(creds_file, scopes=_SCOPES)
    _cached_creds = creds
    _cached_spreadsheet_id = spreadsheet_id
    _cached_creds_loaded_at = now
    logger.debug("Google Sheets credentials loaded from %s", creds_file)
    return creds, spreadsheet_id


def _max_cells_per_request() -> int:
    """Cells per API call; readable at runtime so tests can lower it."""
    raw = os.environ.get("STUDYSYNC_SHEETS_MAX_CELLS_PER_REQUEST", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_CELLS_PER_REQUEST
    return max(value, 1)


def _write_in_chunks(
    worksheet, rows: list[list], max_cells_per_request: int
) -> None:
    """Write *rows* (header + data) to *worksheet* in size-safe chunks.

    Google's `spreadsheets.values.update` rejects a request that exceeds
    roughly 500k cells or ~10 MB of payload. A single growing tab (e.g.
    attendance) can exceed that one day, so the rows are split so each API
    call stays well under the cap. Each chunk is written at its own A1
    range; Sheets auto-expands the grid to fit.
    """
    ncols = max(len(rows[0]) if rows else 0, 1)
    rows_per_chunk = max(max_cells_per_request // ncols, 1)

    for start in range(0, len(rows), rows_per_chunk):
        chunk = rows[start : start + rows_per_chunk]
        top = start + 1  # 1-based spreadsheet row for this chunk's first row
        worksheet.update(range_name=f"A{top}", values=chunk)


def write_sheet(sheet_name: str, headers: list[str], data: list[list]) -> int:
    """
    Full-rewrite: clear *sheet_name* then write headers + all rows.

    Returns the number of data rows written (excludes the header row).
    Creates the worksheet if it doesn't exist. Oversized tabs are written
    in chunks (see _write_in_chunks) so no single API call can exceed
    Google's per-request limits.
    """
    creds, spreadsheet_id = _get_credentials()
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(spreadsheet_id)

    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=sheet_name,
            rows=max(len(data) + 10, 100),
            cols=len(headers),
        )

    rows = [headers] + data
    _write_in_chunks(worksheet, rows, _max_cells_per_request())
    return len(data)
