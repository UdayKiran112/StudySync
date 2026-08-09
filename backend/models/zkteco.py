"""
models/zkteco.py
------------------
Pydantic response models for the ZKTeco device integration endpoints.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


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


class ZkSyncReport(BaseModel):
    """Durable device sync health: ledger pending count, per-state ledger
    breakdown, last reconcile, and whether the device buffer is fully
    consumed."""

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
    fully_synced: bool = False


class ZkLiveStatus(BaseModel):
    mode: str
    connected: bool
    last_event_at: Optional[datetime] = None
    last_payload: Optional[dict] = None
    last_outcome: Optional[str] = None
    last_error: Optional[str] = None
