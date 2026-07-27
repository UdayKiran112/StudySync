"""
models/subscriptions.py
------------------------
Subscriptions are the catalog of library-owned digital resources
(e.g. "SUB001" -> JSTOR). This table is referenced by
digital_library_usage.subscription_id whenever account_type =
'Library Subscription'.

subscription_id is staff-assigned (e.g. "SUB001"), not auto-generated --
matches the schema's plain TEXT PRIMARY KEY with no sequence.

start_date mirrors students.join_date: a permanent historical fact
(when the subscription was purchased/activated) that's never overwritten.
Combined with validity_days, it drives an automatically computed
valid_until and status, the same way join_date + renewal_count does for
students -- see routers/subscriptions.py.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import date as date_type
from models.common import RequestModel


class SubscriptionCreate(RequestModel):
    subscription_id: str = Field(
        ..., min_length=1, description="Staff-assigned code, e.g. 'SUB001'"
    )
    name: str = Field(..., min_length=1, description="e.g. 'JSTOR', 'Sreedhar CCE'")
    type: Optional[str] = Field(
        None, description="e.g. 'Online Learning', 'Video Platform'"
    )
    cost: Optional[float] = Field(None, ge=0)
    start_date: date_type = Field(
        ..., description="When this subscription was purchased/activated."
    )
    validity_days: Optional[int] = Field(None, gt=0)
    status: Literal["Active", "Expired"] = "Active"


class SubscriptionUpdate(RequestModel):
    """All fields optional -- only supplied fields are changed."""

    name: Optional[str] = Field(default=None, min_length=1)
    type: Optional[str] = None
    cost: Optional[float] = Field(None, ge=0)
    start_date: Optional[date_type] = None
    validity_days: Optional[int] = Field(None, gt=0)
    status: Optional[Literal["Active", "Expired"]] = None


class SubscriptionResponse(BaseModel):
    subscription_id: str
    name: str
    type: Optional[str] = None
    cost: Optional[float] = None
    start_date: date_type
    validity_days: Optional[int] = None
    status: str
    valid_until: Optional[date_type] = Field(
        None,
        description="start_date + validity_days -- computed by the DB, null if validity_days isn't set.",
    )
