"""
sheets_client.py
----------------
Thin wrapper around gspread for full-rewrite sheet sync.

Configured via environment variables:
    GOOGLE_SPREADSHEET_ID  – the target Google Sheet's ID (required)
    GOOGLE_CREDS_FILE      – path to a service-account JSON key (default: credentials.json)

The Google Sheet must be shared with the service-account email
(found inside the JSON key file) with Editor access.
"""

import os

import gspread
from google.oauth2.service_account import Credentials


class SheetsConfigError(Exception):
    """Raised when Google Sheets configuration is missing or invalid."""


_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_credentials() -> tuple[Credentials, str]:
    """Load credentials and return (creds, spreadsheet_id)."""
    spreadsheet_id = os.environ.get("GOOGLE_SPREADSHEET_ID", "")
    if not spreadsheet_id:
        raise SheetsConfigError(
            "GOOGLE_SPREADSHEET_ID environment variable is not set."
        )

    creds_file = os.environ.get("GOOGLE_CREDS_FILE", "credentials.json")
    if not os.path.isfile(creds_file):
        raise SheetsConfigError(
            f"Service-account key not found: {creds_file}. "
            "Set GOOGLE_CREDS_FILE or place credentials.json in the working directory."
        )

    creds = Credentials.from_service_account_file(creds_file, scopes=_SCOPES)
    return creds, spreadsheet_id


def write_sheet(sheet_name: str, headers: list[str], data: list[list]) -> int:
    """
    Full-rewrite: clear *sheet_name* then write headers + all rows.

    Returns the number of data rows written (excludes the header row).
    Creates the worksheet if it doesn't exist.
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
    worksheet.update(range_name="A1", values=rows)
    return len(data)
