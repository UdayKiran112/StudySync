"""CRUD endpoints for exams and the marks students earn in them."""

import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db_dependency
from models.exams import (
    ExamCreate,
    ExamMarkCreate,
    ExamMarkResponse,
    ExamMarkUpdate,
    ExamResponse,
    ExamUpdate,
)
from security import require_api_key


router = APIRouter(
    prefix="/api/exams", tags=["Exams"], dependencies=[Depends(require_api_key)]
)
marks_router = APIRouter(
    prefix="/api/exam-marks", tags=["Exam Marks"], dependencies=[Depends(require_api_key)]
)


def _get_exam(db: sqlite3.Connection, exam_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Exam {exam_id} not found")
    return row


def _ensure_active_student(db: sqlite3.Connection, student_id: int) -> None:
    row = db.execute(
        "SELECT student_id, status FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")
    if row["status"] != "Active":
        raise HTTPException(status_code=400, detail=f"Student {student_id} is inactive")


def _ensure_marks_within_max(
    db: sqlite3.Connection, exam_id: int, marks_obtained: float, max_marks: float
) -> None:
    if marks_obtained > max_marks:
        raise HTTPException(
            status_code=422,
            detail=f"marks_obtained cannot exceed this exam's max_marks ({max_marks})",
        )


@router.post("", response_model=ExamResponse, status_code=201)
def create_exam(exam: ExamCreate, db: sqlite3.Connection = Depends(get_db_dependency)):
    cursor = db.execute(
        """
        INSERT INTO exams (exam_name, exam_date, subject, max_marks)
        VALUES (?, ?, ?, ?)
        """,
        (exam.exam_name, exam.exam_date, exam.subject, exam.max_marks),
    )
    row = _get_exam(db, cursor.lastrowid)
    return dict(row)


@router.get("", response_model=List[ExamResponse])
def list_exams(
    subject: Optional[str] = None,
    date_: Optional[date] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    query = "SELECT * FROM exams WHERE 1=1"
    params = []
    if subject:
        query += " AND subject = ?"
        params.append(subject)
    if date_ is not None:
        query += " AND exam_date = ?"
        params.append(date_)
    if search:
        query += " AND (exam_name LIKE ? OR subject LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    query += " ORDER BY exam_date DESC, exam_id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [dict(row) for row in db.execute(query, params).fetchall()]


@router.get("/{exam_id}", response_model=ExamResponse)
def get_exam(exam_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    return dict(_get_exam(db, exam_id))


@router.patch("/{exam_id}", response_model=ExamResponse)
def update_exam(
    exam_id: int,
    exam: ExamUpdate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    existing = _get_exam(db, exam_id)
    updates = exam.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")
    if "exam_name" in updates and updates["exam_name"] is None:
        raise HTTPException(status_code=422, detail="exam_name cannot be null")
    if "max_marks" in updates and updates["max_marks"] is None:
        raise HTTPException(status_code=422, detail="max_marks cannot be null")

    new_max_marks = updates.get("max_marks", existing["max_marks"])
    highest_mark = db.execute(
        "SELECT MAX(marks_obtained) AS highest_mark FROM exam_marks WHERE exam_id = ?",
        (exam_id,),
    ).fetchone()["highest_mark"]
    if highest_mark is not None and highest_mark > new_max_marks:
        raise HTTPException(
            status_code=422,
            detail=(
                f"max_marks cannot be below an existing mark ({highest_mark}). "
                "Update or remove that mark first."
            ),
        )

    set_clause = ", ".join(f"{field} = ?" for field in updates)
    db.execute(
        f"UPDATE exams SET {set_clause} WHERE exam_id = ?",
        [*updates.values(), exam_id],
    )
    return dict(_get_exam(db, exam_id))


@router.delete("/{exam_id}", status_code=204)
def delete_exam(exam_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    _get_exam(db, exam_id)
    try:
        db.execute("DELETE FROM exams WHERE exam_id = ?", (exam_id,))
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete exam {exam_id}: student marks exist. "
                "Delete the marks first."
            ),
        )
    return None


@router.post("/{exam_id}/marks", response_model=ExamMarkResponse, status_code=201)
def create_exam_mark(
    exam_id: int,
    mark: ExamMarkCreate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    exam = _get_exam(db, exam_id)
    _ensure_active_student(db, mark.student_id)
    _ensure_marks_within_max(db, exam_id, mark.marks_obtained, exam["max_marks"])
    try:
        cursor = db.execute(
            """
            INSERT INTO exam_marks (student_id, exam_id, marks_obtained, remarks)
            VALUES (?, ?, ?, ?)
            """,
            (mark.student_id, exam_id, mark.marks_obtained, mark.remarks),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Student {mark.student_id} already has marks for exam {exam_id}",
        )
    row = db.execute("SELECT * FROM exam_marks WHERE mark_id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@router.get("/{exam_id}/marks", response_model=List[ExamMarkResponse])
def list_marks_for_exam(
    exam_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    _get_exam(db, exam_id)
    rows = db.execute(
        """
        SELECT * FROM exam_marks WHERE exam_id = ?
        ORDER BY marks_obtained DESC, mark_id ASC LIMIT ? OFFSET ?
        """,
        (exam_id, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


@marks_router.get("", response_model=List[ExamMarkResponse])
def list_exam_marks(
    student_id: Optional[int] = None,
    exam_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    query = "SELECT * FROM exam_marks WHERE 1=1"
    params = []
    if student_id is not None:
        query += " AND student_id = ?"
        params.append(student_id)
    if exam_id is not None:
        query += " AND exam_id = ?"
        params.append(exam_id)
    query += " ORDER BY exam_id DESC, marks_obtained DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [dict(row) for row in db.execute(query, params).fetchall()]


def _get_mark(db: sqlite3.Connection, mark_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM exam_marks WHERE mark_id = ?", (mark_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Exam mark {mark_id} not found")
    return row


@marks_router.get("/{mark_id}", response_model=ExamMarkResponse)
def get_exam_mark(mark_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    return dict(_get_mark(db, mark_id))


@marks_router.patch("/{mark_id}", response_model=ExamMarkResponse)
def update_exam_mark(
    mark_id: int,
    mark: ExamMarkUpdate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    existing = _get_mark(db, mark_id)
    updates = mark.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")
    if "marks_obtained" in updates and updates["marks_obtained"] is None:
        raise HTTPException(status_code=422, detail="marks_obtained cannot be null")
    if "marks_obtained" in updates:
        exam = _get_exam(db, existing["exam_id"])
        _ensure_marks_within_max(
            db, existing["exam_id"], updates["marks_obtained"], exam["max_marks"]
        )

    set_clause = ", ".join(f"{field} = ?" for field in updates)
    db.execute(
        f"UPDATE exam_marks SET {set_clause} WHERE mark_id = ?",
        [*updates.values(), mark_id],
    )
    return dict(_get_mark(db, mark_id))


@marks_router.delete("/{mark_id}", status_code=204)
def delete_exam_mark(mark_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    _get_mark(db, mark_id)
    db.execute("DELETE FROM exam_marks WHERE mark_id = ?", (mark_id,))
    return None
