"""
routers/subscriptions.py
--------------------------
CRUD for the subscriptions catalog. This is a small reference table —
staff add a subscription once (e.g. "SUB001" -> JSTOR), then it gets
referenced by digital_library_usage.subscription_id whenever a student
uses a Library Subscription account.

Deleting a subscription that's already been used in digital_library_usage
will fail (ON DELETE RESTRICT) — prefer setting status='Expired' instead,
same convention as students.status.
"""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional

from database import get_db_dependency
from models.subscriptions import (
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionResponse,
)
from security import require_api_key

router = APIRouter(
    prefix="/api/subscriptions",
    tags=["Subscriptions"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=SubscriptionResponse, status_code=201)
def create_subscription(
    subscription: SubscriptionCreate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """Add a new subscription to the catalog. 409 if the ID is already taken."""
    existing = db.execute(
        "SELECT subscription_id FROM subscriptions WHERE subscription_id = ?",
        (subscription.subscription_id,),
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Subscription ID '{subscription.subscription_id}' already exists",
        )

    db.execute(
        """
        INSERT INTO subscriptions (subscription_id, name, type, cost, validity_days, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            subscription.subscription_id,
            subscription.name,
            subscription.type,
            subscription.cost,
            subscription.validity_days,
            subscription.status,
        ),
    )

    row = db.execute(
        "SELECT * FROM subscriptions WHERE subscription_id = ?",
        (subscription.subscription_id,),
    ).fetchone()
    return dict(row)


@router.get("", response_model=List[SubscriptionResponse])
def list_subscriptions(
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """List all subscriptions, optionally filtered by status and/or name search."""
    query = "SELECT * FROM subscriptions WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)

    if search:
        query += " AND name LIKE ?"
        params.append(f"%{search}%")

    query += " ORDER BY name LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/{subscription_id}", response_model=SubscriptionResponse)
def get_subscription(
    subscription_id: str, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Fetch a single subscription by ID."""
    row = db.execute(
        "SELECT * FROM subscriptions WHERE subscription_id = ?", (subscription_id,)
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404, detail=f"Subscription '{subscription_id}' not found"
        )
    return dict(row)


@router.patch("/{subscription_id}", response_model=SubscriptionResponse)
def update_subscription(
    subscription_id: str,
    subscription: SubscriptionUpdate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Partially update a subscription — e.g. mark it 'Expired', change cost,
    or rename it. Only fields provided in the request body are changed.
    """
    existing = db.execute(
        "SELECT * FROM subscriptions WHERE subscription_id = ?", (subscription_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"Subscription '{subscription_id}' not found"
        )

    updates = subscription.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is None:
        raise HTTPException(status_code=422, detail="name cannot be null")
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    set_clause = ", ".join(f"{field} = ?" for field in updates.keys())
    values = list(updates.values()) + [subscription_id]

    db.execute(
        f"UPDATE subscriptions SET {set_clause} WHERE subscription_id = ?", values
    )

    row = db.execute(
        "SELECT * FROM subscriptions WHERE subscription_id = ?", (subscription_id,)
    ).fetchone()
    return dict(row)


@router.delete("/{subscription_id}", status_code=204)
def delete_subscription(
    subscription_id: str, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Delete a subscription. Fails with 409 if any digital_library_usage
    records reference it (ON DELETE RESTRICT) — set status='Expired'
    instead to preserve history.
    """
    existing = db.execute(
        "SELECT * FROM subscriptions WHERE subscription_id = ?", (subscription_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"Subscription '{subscription_id}' not found"
        )

    try:
        db.execute(
            "DELETE FROM subscriptions WHERE subscription_id = ?", (subscription_id,)
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete subscription '{subscription_id}': it has been used in "
                "digital library records. Set status to 'Expired' instead."
            ),
        )
    return None
