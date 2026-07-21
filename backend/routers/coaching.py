"""Coaching class setup and mixed library/external participant rosters."""

import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db_dependency
from models.coaching import (CoachingClassCreate, CoachingClassResponse, CoachingClassUpdate,
                             CoachingEnrollmentCreate, CoachingEnrollmentResponse, CoachingEnrollmentUpdate)
from security import require_api_key

router = APIRouter(prefix="/api/coaching-classes", tags=["Coaching Classes"], dependencies=[Depends(require_api_key)])


def _class_or_404(db, class_id):
    row = db.execute("SELECT * FROM coaching_classes WHERE class_id = ?", (class_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Coaching class {class_id} not found")
    return row


def _enrollment_row(db, enrollment_id):
    row = db.execute("""SELECT enrollment.*, COALESCE(students.name, enrollment.external_name) AS participant_name
        FROM coaching_enrollments AS enrollment LEFT JOIN students ON students.student_id = enrollment.student_id
        WHERE enrollment.enrollment_id = ?""", (enrollment_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Coaching enrollment {enrollment_id} not found")
    return row


@router.post("", response_model=CoachingClassResponse, status_code=201)
def create_class(payload: CoachingClassCreate, db: sqlite3.Connection = Depends(get_db_dependency)):
    cursor = db.execute("""INSERT INTO coaching_classes
        (title, class_date, start_time, end_time, subject, instructor, venue, capacity, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", tuple(payload.model_dump().values()))
    return dict(_class_or_404(db, cursor.lastrowid))


@router.get("", response_model=List[CoachingClassResponse])
def list_classes(date_: Optional[date] = None, search: Optional[str] = None,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 db: sqlite3.Connection = Depends(get_db_dependency)):
    query, params = "SELECT * FROM coaching_classes WHERE 1=1", []
    if date_:
        query += " AND class_date = ?"; params.append(date_)
    if search:
        query += " AND (title LIKE ? OR subject LIKE ? OR instructor LIKE ?)"; params.extend([f"%{search}%"] * 3)
    query += " ORDER BY class_date DESC, start_time DESC, class_id DESC LIMIT ? OFFSET ?"; params.extend([limit, offset])
    return [dict(row) for row in db.execute(query, params).fetchall()]


@router.get("/{class_id}", response_model=CoachingClassResponse)
def get_class(class_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    return dict(_class_or_404(db, class_id))


@router.patch("/{class_id}", response_model=CoachingClassResponse)
def update_class(class_id: int, payload: CoachingClassUpdate, db: sqlite3.Connection = Depends(get_db_dependency)):
    _class_or_404(db, class_id)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields provided to update")
    db.execute(f"UPDATE coaching_classes SET {', '.join(f'{key} = ?' for key in updates)} WHERE class_id = ?", [*updates.values(), class_id])
    return dict(_class_or_404(db, class_id))


@router.delete("/{class_id}", status_code=204)
def delete_class(class_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    _class_or_404(db, class_id)
    db.execute("DELETE FROM coaching_classes WHERE class_id = ?", (class_id,))


@router.get("/{class_id}/enrollments", response_model=List[CoachingEnrollmentResponse])
def list_enrollments(class_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    _class_or_404(db, class_id)
    rows = db.execute("""SELECT enrollment.*, COALESCE(students.name, enrollment.external_name) AS participant_name
        FROM coaching_enrollments AS enrollment LEFT JOIN students ON students.student_id = enrollment.student_id
        WHERE enrollment.class_id = ? ORDER BY participant_name COLLATE NOCASE""", (class_id,)).fetchall()
    return [dict(row) for row in rows]


@router.post("/{class_id}/enrollments", response_model=CoachingEnrollmentResponse, status_code=201)
def add_enrollment(class_id: int, payload: CoachingEnrollmentCreate, db: sqlite3.Connection = Depends(get_db_dependency)):
    coaching_class = _class_or_404(db, class_id)
    if payload.student_id is not None:
        student = db.execute("SELECT student_id, status FROM students WHERE student_id = ?", (payload.student_id,)).fetchone()
        if not student:
            raise HTTPException(404, f"Student {payload.student_id} not found")
        if student["status"] != "Active":
            raise HTTPException(400, f"Student {payload.student_id} is inactive")
    enrolled = db.execute("SELECT COUNT(*) FROM coaching_enrollments WHERE class_id = ? AND attendance_status != 'Cancelled'", (class_id,)).fetchone()[0]
    if coaching_class["capacity"] is not None and enrolled >= coaching_class["capacity"]:
        raise HTTPException(409, "This coaching class has reached its capacity")
    values = payload.model_dump()
    try:
        cursor = db.execute("""INSERT INTO coaching_enrollments
            (class_id, participant_type, student_id, external_name, village, phone, gender, guardian_name, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (class_id, *values.values()))
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "This library student is already registered for the class") from error
    return dict(_enrollment_row(db, cursor.lastrowid))


@router.patch("/enrollments/{enrollment_id}", response_model=CoachingEnrollmentResponse)
def update_enrollment(enrollment_id: int, payload: CoachingEnrollmentUpdate, db: sqlite3.Connection = Depends(get_db_dependency)):
    _enrollment_row(db, enrollment_id)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields provided to update")
    db.execute(f"UPDATE coaching_enrollments SET {', '.join(f'{key} = ?' for key in updates)} WHERE enrollment_id = ?", [*updates.values(), enrollment_id])
    return dict(_enrollment_row(db, enrollment_id))


@router.delete("/enrollments/{enrollment_id}", status_code=204)
def delete_enrollment(enrollment_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    _enrollment_row(db, enrollment_id)
    db.execute("DELETE FROM coaching_enrollments WHERE enrollment_id = ?", (enrollment_id,))
