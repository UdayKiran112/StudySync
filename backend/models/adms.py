"""
models/adms.py
----------------
Pydantic response models for the staff-facing ADMS diagnostic endpoint
(GET /api/adms/status). The device-facing /iclock/* endpoints in
routers/adms.py are NOT modeled here -- they speak ZKTeco's plain-text
protocol, not JSON, and must return exactly what the device firmware
expects (see routers/adms.py).
"""

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel


class AdmsPushResult(BaseModel):
    pulled: int
    imported: int
    duplicates: int
    duplicate_transport: int = 0
    duplicate_debounced: int = 0
    unknown_students: int
    renewed: int


class AdmsDeviceStatus(BaseModel):
    device_serial: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    last_reconcile_at: Optional[datetime] = None
    last_buffer_count: Optional[int] = None
    ledger_pending: int = 0
    last_handshake_at: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    last_push_at: Optional[datetime] = None
    last_result: Optional[AdmsPushResult] = None


class AdmsStatus(BaseModel):
    devices: Dict[str, AdmsDeviceStatus]
