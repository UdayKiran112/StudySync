"""
models/subscriptions.py
------------------------
Subscriptions are the catalog of library-owned digital resources
(e.g. "SUB001" -> JSTOR). This table is referenced by
digital_library_usage.subscription_id whenever account_type =
'Library Subscription'.

subscription_id is staff-assigned (e.g. "SUB001"), not auto-generated —
matches the schema's plain TEXT PRIMARY KEY with no sequence.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal


class SubscriptionCreate(BaseModel):
    subscription_id: str = Field(..., description="Staff-assigned code, e.g. 'SUB001'")
    name: str = Field(..., description="e.g. 'JSTOR', 'Sreedhar CCE'")
    type: Optional[str] = Field(
        None, description="e.g. 'Online Learning', 'Video Platform'"
    )
    cost: Optional[float] = Field(None, ge=0)
    validity_days: Optional[int] = Field(None, gt=0)
    status: Literal["Active", "Expired"] = "Active"


class SubscriptionUpdate(BaseModel):
    """All fields optional — only supplied fields are changed."""

    name: Optional[str] = Field(default=None, min_length=1)
    type: Optional[str] = None
    cost: Optional[float] = Field(None, ge=0)
    validity_days: Optional[int] = Field(None, gt=0)
    status: Optional[Literal["Active", "Expired"]] = None


class SubscriptionResponse(BaseModel):
    subscription_id: str
    name: str
    type: Optional[str] = None
    cost: Optional[float] = None
    validity_days: Optional[int] = None
    status: str
