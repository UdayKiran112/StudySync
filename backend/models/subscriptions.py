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
from typing import Literal
from datetime import date as date_type
from models.common import RequestModel


class SubscriptionCreate(RequestModel):
    subscription_id: str = Field(
        ..., min_length=1, description="Staff-assigned code, e.g. 'SUB001'"
    )
    name: str = Field(..., min_length=1, description="e.g. 'JSTOR', 'Sreedhar CCE'")
    type: str | None = Field(
        default=None, description="e.g. 'Online Learning', 'Video Platform'"
    )
    cost: float | None = Field(default=None, ge=0)
    start_date: date_type = Field(
        ..., description="When this subscription was purchased/activated."
    )
    validity_days: int | None = Field(None, gt=0)
    status: Literal["Active", "Expired"] = "Active"


class SubscriptionUpdate(RequestModel):
    """All fields optional -- only supplied fields are changed."""

    name: str | None = Field(default=None, min_length=1)
    type: str | None = None
    cost: float | None = Field(None, ge=0)
    start_date: date_type | None = None
    validity_days: int | None = Field(None, gt=0)
    status: Literal["Active", "Expired"] | None = None


class SubscriptionResponse(BaseModel):
    subscription_id: str
    name: str
    type: str | None = None
    cost: float | None = None
    start_date: date_type
    validity_days: int | None = None
    status: str
    valid_until: date_type | None = Field(
        None,
        description="start_date + validity_days -- computed by the DB, null if validity_days isn't set.",
    )
