from datetime import date, datetime
from typing import Literal, Optional
from pydantic import Field, model_validator
from models.common import RequestModel

class OtherActivityCreate(RequestModel):
    session_name: str = Field(..., min_length=1)
    speaker_name: str = Field(..., min_length=1)
    session_date: date
    session_type: str = Field(..., min_length=1)
    notes: Optional[str] = None

class OtherActivityUpdate(OtherActivityCreate):
    pass

class OtherActivityResponse(OtherActivityCreate):
    activity_id: int
    created_at: datetime

class OtherActivityAttendanceCreate(RequestModel):
    participant_type: Literal['Library Student', 'External Student']
    student_id: Optional[int] = None
    external_participant_id: Optional[int] = None
    
    @model_validator(mode='after')
    def participant_matches_type(self):
        if self.participant_type == 'Library Student' and self.student_id is None:
            raise ValueError('student_id is required')
        if self.participant_type == 'External Student' and self.external_participant_id is None:
            raise ValueError('external_participant_id is required')
        return self

class OtherActivityAttendanceResponse(RequestModel):
    attendance_id: int
    activity_id: int
    participant_type: str
    student_id: Optional[int] = None
    external_participant_id: Optional[int] = None
    attended_at: datetime
    participant_name: str
    village: Optional[str] = None
    phone: Optional[str] = None
