from datetime import date, datetime
from typing import Literal, Optional
from pydantic import Field, model_validator
from models.common import RequestModel

class InstructorCreate(RequestModel):
    name: str = Field(..., min_length=1); phone: Optional[str] = None; specialization: Optional[str] = None; notes: Optional[str] = None
class InstructorResponse(InstructorCreate):
    instructor_id: int; status: str
class ExternalParticipantCreate(RequestModel):
    name: str = Field(..., min_length=1); village: str = Field(..., min_length=1); phone: Optional[str] = None; gender: Optional[Literal['Male','Female','Other']] = None; guardian_name: Optional[str] = None; notes: Optional[str] = None
class ExternalParticipantResponse(ExternalParticipantCreate):
    external_participant_id: int; created_at: datetime
class CoachingClassCreate(RequestModel):
    title: str = Field(..., min_length=1); class_date: date; start_time: Optional[str] = None; end_time: Optional[str] = None; subject: Optional[str] = None; instructor_id: Optional[int] = None; notes: Optional[str] = None
class CoachingClassUpdate(CoachingClassCreate): pass
class CoachingClassResponse(CoachingClassCreate):
    class_id: int; duration_minutes: Optional[int] = None; created_at: datetime; instructor_name: Optional[str] = None
class CoachingEnrollmentCreate(RequestModel):
    participant_type: Literal['Library Student','External Student']; student_id: Optional[int] = None; external_participant_id: Optional[int] = None
    @model_validator(mode='after')
    def participant_matches_type(self):
        if self.participant_type == 'Library Student' and self.student_id is None: raise ValueError('student_id is required')
        if self.participant_type == 'External Student' and self.external_participant_id is None: raise ValueError('external_participant_id is required')
        return self
class CoachingEnrollmentResponse(RequestModel):
    enrollment_id: int; class_id: int; participant_type: str; student_id: Optional[int] = None; external_participant_id: Optional[int] = None; enrolled_at: datetime; participant_name: str; village: Optional[str] = None; phone: Optional[str] = None
