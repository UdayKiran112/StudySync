"""
routers/students.py
--------------------
CRUD endpoints for the students table.

Every other table (attendance, digital_library_usage, exam_marks, etc.)
references student_id as a foreign key, so this is the first module to
get working and confirm end-to-end before building anything else.
"""

import sqlite3
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional

from database import get_db_dependency
from models.students import StudentCreate, StudentUpdate, StudentResponse
from security import require_api_key

router = APIRouter(
    prefix="/api/students", tags=["Students"], dependencies=[Depends(require_api_key)]
)

# Membership validity: join_date + (renewal_count + 1) whole years.
# renewal_count starts at 0, so a never-renewed student's expiry is
# join_date + 1 year, exactly as before -- but now each renewal adds a
# year to this formula instead of resetting join_date itself. Defined
# once here and reused everywhere this app needs "when does this
# student's membership expire" so it can never drift out of sync
# between endpoints (or with database.py's per-connection status sync).
VALID_UNTIL_EXPR = "date(join_date, '+' || (renewal_count + 1) || ' years')"


@router.post("/{student_id}/renew", response_model=StudentResponse)
def renew_student(student_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    """
    Renew a membership for one more year WITHOUT touching join_date.

    join_date is a permanent historical fact (when the student first
    joined) and must never change. Renewing just increments
    renewal_count by one, which extends membership validity by exactly
    one more year, always anchored to the original join_date's calendar
    day (see VALID_UNTIL_EXPR below) -- not to whenever the renewal
    happens to be clicked.
    """
    existing = db.execute(
        "SELECT student_id FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")

    # renewal_count and status are set in the same statement. SQLite
    # evaluates every expression in a SET clause against the row's OLD
    # values, so "the new renewal_count" is spelled out explicitly here
    # as (renewal_count + 1) rather than relying on the just-updated
    # value -- this keeps the math correct without a fragile string
    # substitution on VALID_UNTIL_EXPR.
    db.execute(
        """UPDATE students
        SET renewal_count = renewal_count + 1,
            status = CASE
                WHEN date(join_date, '+' || ((renewal_count + 1) + 1) || ' years') < ?
                THEN 'Inactive' ELSE 'Active'
            END
        WHERE student_id = ?""",
        (date.today().isoformat(), student_id),
    )
    row = db.execute(
        f"SELECT *, {VALID_UNTIL_EXPR} AS valid_until FROM students WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    return dict(row)


@router.post("", response_model=StudentResponse, status_code=201)
def create_student(
    student: StudentCreate, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Create a new student record."""
    existing = db.execute(
        "SELECT student_id FROM students WHERE student_id = ?", (student.student_id,)
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=409, detail=f"Student ID {student.student_id} already exists"
        )

    db.execute(
        """
        INSERT INTO students (student_id, name, gender, date_of_birth, phone, email,
                               father_name, qualification, goal, preparing_for,
                               address, join_date, photo_path, status, renewal_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            student.student_id,
            student.name,
            student.gender,
            student.date_of_birth,
            student.phone,
            student.email,
            student.father_name,
            student.qualification,
            student.goal,
            student.preparing_for,
            student.address,
            student.join_date,
            student.photo_path,
            student.status,
            student.renewal_count or 0,
        ),
    )
    # A back-dated record should immediately reflect the same rule.
    db.execute(
        f"""UPDATE students
        SET status = CASE WHEN {VALID_UNTIL_EXPR} < ? THEN 'Inactive' ELSE 'Active' END
        WHERE student_id = ?""",
        (date.today().isoformat(), student.student_id),
    )

    row = db.execute(
        f"SELECT *, {VALID_UNTIL_EXPR} AS valid_until FROM students WHERE student_id = ?",
        (student.student_id,),
    ).fetchone()
    return dict(row)


@router.get("", response_model=List[StudentResponse])
def list_students(
    status: Optional[str] = None,
    search: Optional[str] = None,
    new_this_month: bool = False,
    expiring: bool = False,
    present_today: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    List all students, optionally filtered by status (Active/Inactive)
    and/or a name search (partial match).
    """
    query = f"SELECT *, {VALID_UNTIL_EXPR} AS valid_until FROM students WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)

    if search:
        query += " AND name LIKE ?"
        params.append(f"%{search}%")

    if new_this_month:
        query += " AND date(join_date) >= date('now', 'start of month')"
    if expiring:
        query += f" AND status = 'Active' AND {VALID_UNTIL_EXPR} >= date('now') AND {VALID_UNTIL_EXPR} <= date('now', '+30 days')"
    if present_today:
        query += " AND student_id IN (SELECT student_id FROM attendance WHERE date = date('now'))"

    query += " ORDER BY name LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/summary")
def student_summary(
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """Filter-aware overview metrics and chart data for the Students page."""
    clauses: list[str] = []
    params: list[str] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if search:
        clauses.append("(name LIKE ? OR CAST(student_id AS TEXT) LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    totals = db.execute(
        f"""SELECT COUNT(*) AS total,
        SUM(CASE WHEN status = 'Active' THEN 1 ELSE 0 END) AS active,
        SUM(CASE WHEN status = 'Inactive' THEN 1 ELSE 0 END) AS inactive,
        SUM(CASE WHEN date(join_date) >= date('now', 'start of month') THEN 1 ELSE 0 END) AS new_this_month,
        SUM(CASE WHEN status = 'Active' AND {VALID_UNTIL_EXPR} >= date('now')
                 AND {VALID_UNTIL_EXPR} <= date('now', '+30 days') THEN 1 ELSE 0 END) AS expiring
        FROM students{where}""",
        params,
    ).fetchone()
    present = db.execute(
        f"""SELECT COUNT(DISTINCT attendance.student_id) AS present
        FROM attendance JOIN students ON students.student_id = attendance.student_id
        {where}{" AND" if where else " WHERE"} attendance.date = date('now')""",
        params,
    ).fetchone()
    gender_rows = db.execute(
        f"""SELECT COALESCE(gender, 'Not specified') AS gender, COUNT(*) AS count
        FROM students{where} GROUP BY COALESCE(gender, 'Not specified') ORDER BY gender""",
        params,
    ).fetchall()
    monthly_rows = db.execute(
        f"""SELECT strftime('%Y-%m', join_date) AS month, COUNT(*) AS count
        FROM students{where}{" AND" if where else " WHERE"} date(join_date) >= date('now', '-5 months', 'start of month')
        GROUP BY month ORDER BY month""",
        params,
    ).fetchall()
    return {
        "total": totals["total"] or 0,
        "active": totals["active"] or 0,
        "inactive": totals["inactive"] or 0,
        "new_this_month": totals["new_this_month"] or 0,
        "expiring": totals["expiring"] or 0,
        "present_today": present["present"] or 0,
        "gender_distribution": [dict(row) for row in gender_rows],
        "monthly_registrations": [dict(row) for row in monthly_rows],
    }


@router.get("/{student_id}", response_model=StudentResponse)
def get_student(student_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    """Fetch a single student by ID."""
    row = db.execute(
        f"SELECT *, {VALID_UNTIL_EXPR} AS valid_until FROM students WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")
    return dict(row)


@router.patch("/{student_id}", response_model=StudentResponse)
def update_student(
    student_id: int,
    student: StudentUpdate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Partially update a student. Only fields provided in the request body
    are changed; omitted fields are left untouched.
    """
    existing = db.execute(
        "SELECT * FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")

    updates = student.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")
    if "name" in updates and updates["name"] is None:
        raise HTTPException(status_code=422, detail="name cannot be null")

    set_clause = ", ".join(f"{field} = ?" for field in updates.keys())
    values = list(updates.values()) + [student_id]

    db.execute(f"UPDATE students SET {set_clause} WHERE student_id = ?", values)

    row = db.execute(
        f"SELECT *, {VALID_UNTIL_EXPR} AS valid_until FROM students WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    return dict(row)


@router.delete("/{student_id}", status_code=204)
def delete_student(
    student_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Delete a student. Will fail with a 409 if the student has any related
    records (attendance, exam marks, etc.) due to ON DELETE RESTRICT.

    In practice, prefer setting status='Inactive' via PATCH instead of
    deleting — this preserves history. Delete is here mainly for
    correcting genuine data-entry mistakes (e.g. wrong student_id typed
    in by accident, no related records yet).
    """
    existing = db.execute(
        "SELECT * FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")

    try:
        db.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete student {student_id}: related records exist "
                "(attendance, library usage, exams, etc.). "
                "Set status to 'Inactive' instead."
            ),
        )
    return None
