"""Response models for the Google Sheets sync endpoint."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class SheetSyncResult(BaseModel):
    sheet_name: str
    status: str  # "Success" or "Failed"
    rows_synced: Optional[int] = None
    error: Optional[str] = None


class SyncResponse(BaseModel):
    status: str  # "Success", "Partial", "Failed"
    synced_at: datetime
    sheets: List[SheetSyncResult]


class SyncLogEntry(BaseModel):
    sync_id: int
    synced_at: datetime
    status: str
    details: Optional[str] = None
