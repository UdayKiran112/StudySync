"""
routers/holidays.py
-------------------
CRUD for one-off library closure days (the "holidays" table). The
standing rule -- Sundays and the 2nd/4th/5th Saturday of each month --
lives in the frontend (frontend/src/lib/holidays.ts) because it's pure
calendar math with no rows behind it. This table holds the *exceptions*
staff record by hand (a festival, a power cut), and the analytics page
combines the two when it decides whether a day was open.

holiday_date is UNIQUE, so a day can't be booked as closed twice.
"""

import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db_dependency
from models.holidays import (
    HolidayCreate,
    HolidayResponse,
    HolidayUpdate,
)
from security import require_api_key

router = APIRouter(
    prefix="/api/holidays",
    tags=["Holidays"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=HolidayResponse, status_code=201)
def create_holiday(
    holiday: HolidayCreate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """Record a day the library is closed. 409 if that date is already a holiday."""
    existing = db.execute(
        "SELECT holiday_id FROM holidays WHERE holiday_date = ?",
        (holiday.holiday_date.isoformat(),),
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"{holiday.holiday_date.isoformat()} is already recorded as a holiday",
        )

    db.execute(
        """
        INSERT INTO holidays (holiday_date, name, notes)
        VALUES (?, ?, ?)
        """,
        (holiday.holiday_date.isoformat(), holiday.name, holiday.notes),
    )
    row = db.execute(
        "SELECT * FROM holidays WHERE holiday_date = ?",
        (holiday.holiday_date.isoformat(),),
    ).fetchone()
    return dict(row)


@router.get("", response_model=list[HolidayResponse])
def list_holidays(
    from_date: Optional[date] = Query(
        default=None,
        description="Only holidays on or after this date (YYYY-MM-DD).",
    ),
    to_date: Optional[date] = Query(
        default=None,
        description="Only holidays on or before this date (YYYY-MM-DD).",
    ),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """List holidays, newest date first, optionally restricted to a date range."""
    query = "SELECT * FROM holidays WHERE 1=1"
    params: list[str] = []
    if from_date is not None:
        query += " AND holiday_date >= ?"
        params.append(from_date.isoformat())
    if to_date is not None:
        query += " AND holiday_date <= ?"
        params.append(to_date.isoformat())
    query += " ORDER BY holiday_date DESC"

    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/{holiday_id}", response_model=HolidayResponse)
def get_holiday(
    holiday_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Fetch a single holiday by ID."""
    row = db.execute(
        "SELECT * FROM holidays WHERE holiday_id = ?", (holiday_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Holiday #{holiday_id} not found")
    return dict(row)


@router.patch("/{holiday_id}", response_model=HolidayResponse)
def update_holiday(
    holiday_id: int,
    holiday: HolidayUpdate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Partially update a holiday -- rename it, add a note, or move the date.
    Moving it onto a date that's already a holiday returns 409.
    """
    existing = db.execute(
        "SELECT * FROM holidays WHERE holiday_id = ?", (holiday_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Holiday #{holiday_id} not found")

    updates = holiday.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is None:
        raise HTTPException(status_code=422, detail="name cannot be null")
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    if "holiday_date" in updates:
        target = updates["holiday_date"].isoformat()
        clash = db.execute(
            "SELECT holiday_id FROM holidays WHERE holiday_date = ? AND holiday_id != ?",
            (target, holiday_id),
        ).fetchone()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"{target} is already recorded as a holiday",
            )
        updates["holiday_date"] = target

    safe_fields = list(updates.keys())
    set_clause = ", ".join(f"{field} = ?" for field in safe_fields)
    values = [updates[field] for field in safe_fields] + [holiday_id]
    db.execute(
        f"UPDATE holidays SET {set_clause} WHERE holiday_id = ?", values
    )

    row = db.execute(
        "SELECT * FROM holidays WHERE holiday_id = ?", (holiday_id,)
    ).fetchone()
    return dict(row)


@router.delete("/{holiday_id}", status_code=204)
def delete_holiday(
    holiday_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Remove a holiday so the day counts as open again for analytics."""
    existing = db.execute(
        "SELECT holiday_id FROM holidays WHERE holiday_id = ?", (holiday_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Holiday #{holiday_id} not found")
    db.execute("DELETE FROM holidays WHERE holiday_id = ?", (holiday_id,))
    return None
