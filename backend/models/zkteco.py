"""
models/zkteco.py
------------------
Pydantic response models for the ZKTeco device integration endpoints.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class ZkDeviceStatus(BaseModel):
    ok: bool
    error: Optional[str] = None


class ZkDeviceInfo(BaseModel):
    device_name: Optional[str] = None
    firmware_version: Optional[str] = None
    serial_number: Optional[str] = None
    platform: Optional[str] = None
    mac: Optional[str] = None
    face_version: Optional[str] = None
    fp_version: Optional[str] = None
    device_time: Optional[datetime] = None

    @field_validator("face_version", "fp_version", mode="before")
    @classmethod
    def _version_to_str(cls, v):
        # pyzk returns these as ints on some firmware (e.g. 7 on MB360);
        # the API contract is a string version, so coerce instead of 500.
        return None if v is None else str(v)


class ZkUser(BaseModel):
    uid: int
    name: str
    privilege: int
    user_id: str
    group_id: str = ""


class ZkAttendanceLog(BaseModel):
    uid: int
    user_id: str
    timestamp: datetime
    status: int


class ZkMemoryUsage(BaseModel):
    users: Optional[int] = None
    users_capacity: Optional[int] = None
    fingers: Optional[int] = None
    fingers_capacity: Optional[int] = None
    faces: Optional[int] = None
    faces_capacity: Optional[int] = None
    records: Optional[int] = None
    records_capacity: Optional[int] = None


class ZkSyncResult(BaseModel):
    pulled: int
    imported: int
    duplicates: int
    duplicate_transport: int = 0
    duplicate_debounced: int = 0
    unknown_students: int
    renewed: int = 0
    incomplete: int
    verify_verified: int = 0
    verify_issue_count: int = 0


class ZkSyncReport(BaseModel):
    """Durable device sync health: ledger pending count, per-state ledger
    breakdown, last reconcile, buffer fill/clear state, and whether the
    device buffer is fully consumed."""

    device_serial: Optional[str] = None
    last_reconcile_at: Optional[datetime] = None
    last_buffer_count: Optional[int] = None
    ledger_pending: int = 0
    ledger_total: int = 0
    ledger_applied: int = 0
    ledger_duplicate_transport: int = 0
    ledger_duplicate_debounced: int = 0
    ledger_duplicate_session: int = 0
    ledger_unknown_student: int = 0
    open_sessions: int = 0
    last_verify_verified: int = 0
    last_verify_issue_count: int = 0
    buffer_capacity: Optional[int] = None
    buffer_status: Optional[str] = None
    buffer_fill_percent: Optional[float] = None
    oldest_buffer_ts: Optional[datetime] = None
    last_archive_path: Optional[str] = None
    last_archive_count: Optional[int] = None
    last_clear_at: Optional[datetime] = None
    clear_failures: int = 0
    fully_synced: bool = False


class ZkBufferClearResult(BaseModel):
    """Outcome of the explicit POST /api/zkteco/attendance/clear action."""

    archived: bool
    cleared: bool
    archive_path: Optional[str] = None
    archive_count: int = 0
    remaining_records: int = 0
    verify_verified: int = 0
    verify_issue_count: int = 0
    buffer_status: str = ""


class ZkLiveStatus(BaseModel):
    mode: str
    connected: bool
    last_event_at: Optional[datetime] = None
    last_payload: Optional[dict] = None
    last_outcome: Optional[str] = None
    last_error: Optional[str] = None
