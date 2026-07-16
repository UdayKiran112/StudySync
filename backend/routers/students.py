"""
routers/students.py
--------------------
CRUD endpoints for the students table.

Every other table (attendance, digital_library_usage, exam_marks, etc.)
references student_id as a foreign key, so this is the first module to
get working and confirm end-to-end before building anything else.
"""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

from database import get_db_dependency
from models.students import StudentCreate, StudentUpdate, StudentResponse

router = APIRouter(prefix="/api/students", tags=["Students"])


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
                               address, join_date, photo_path, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            student.student_id,
            student.name,
            student.gender,
            student.date_of_birth,
            student.phone,
            student.email,
            student.address,
            student.join_date,
            student.photo_path,
            student.status,
        ),
    )

    row = db.execute(
        "SELECT * FROM students WHERE student_id = ?", (student.student_id,)
    ).fetchone()
    return dict(row)


@router.get("", response_model=List[StudentResponse])
def list_students(
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    List all students, optionally filtered by status (Active/Inactive)
    and/or a name search (partial match).
    """
    query = "SELECT * FROM students WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)

    if search:
        query += " AND name LIKE ?"
        params.append(f"%{search}%")

    query += " ORDER BY name"

    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/{student_id}", response_model=StudentResponse)
def get_student(student_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    """Fetch a single student by ID."""
    row = db.execute(
        "SELECT * FROM students WHERE student_id = ?", (student_id,)
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

    set_clause = ", ".join(f"{field} = ?" for field in updates.keys())
    values = list(updates.values()) + [student_id]

    db.execute(f"UPDATE students SET {set_clause} WHERE student_id = ?", values)

    row = db.execute(
        "SELECT * FROM students WHERE student_id = ?", (student_id,)
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
