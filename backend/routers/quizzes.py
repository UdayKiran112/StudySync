"""CRUD endpoints for quizzes and the scores students earn in them."""

import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db_dependency
from models.quizzes import (
    QuizCreate,
    QuizResponse,
    QuizScoreCreate,
    QuizScoreResponse,
    QuizScoreUpdate,
    QuizUpdate,
)
from security import require_api_key


router = APIRouter(
    prefix="/api/quizzes", tags=["Quizzes"], dependencies=[Depends(require_api_key)]
)
scores_router = APIRouter(
    prefix="/api/quiz-scores", tags=["Quiz Scores"], dependencies=[Depends(require_api_key)]
)


def _get_quiz(db: sqlite3.Connection, quiz_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM quizzes WHERE quiz_id = ?", (quiz_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Quiz {quiz_id} not found")
    return row


def _ensure_active_student(db: sqlite3.Connection, student_id: int) -> None:
    row = db.execute(
        "SELECT student_id, status FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")
    if row["status"] != "Active":
        raise HTTPException(status_code=400, detail=f"Student {student_id} is inactive")


def _ensure_score_within_max(score: float, max_marks: float) -> None:
    if score > max_marks:
        raise HTTPException(
            status_code=422,
            detail=f"score cannot exceed this quiz's max_marks ({max_marks})",
        )


@router.post("", response_model=QuizResponse, status_code=201)
def create_quiz(quiz: QuizCreate, db: sqlite3.Connection = Depends(get_db_dependency)):
    cursor = db.execute(
        """
        INSERT INTO quizzes (quiz_name, quiz_date, subject, max_marks)
        VALUES (?, ?, ?, ?)
        """,
        (quiz.quiz_name, quiz.quiz_date, quiz.subject, quiz.max_marks),
    )
    return dict(_get_quiz(db, cursor.lastrowid))


@router.get("", response_model=List[QuizResponse])
def list_quizzes(
    subject: Optional[str] = None,
    date_: Optional[date] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    query = "SELECT * FROM quizzes WHERE 1=1"
    params = []
    if subject:
        query += " AND subject = ?"
        params.append(subject)
    if date_ is not None:
        query += " AND quiz_date = ?"
        params.append(date_)
    if search:
        query += " AND (quiz_name LIKE ? OR subject LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    query += " ORDER BY quiz_date DESC, quiz_id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [dict(row) for row in db.execute(query, params).fetchall()]


@router.get("/{quiz_id}", response_model=QuizResponse)
def get_quiz(quiz_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    return dict(_get_quiz(db, quiz_id))


@router.patch("/{quiz_id}", response_model=QuizResponse)
def update_quiz(
    quiz_id: int,
    quiz: QuizUpdate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    existing = _get_quiz(db, quiz_id)
    updates = quiz.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")
    if "quiz_name" in updates and updates["quiz_name"] is None:
        raise HTTPException(status_code=422, detail="quiz_name cannot be null")
    if "max_marks" in updates and updates["max_marks"] is None:
        raise HTTPException(status_code=422, detail="max_marks cannot be null")

    new_max_marks = updates.get("max_marks", existing["max_marks"])
    highest_score = db.execute(
        "SELECT MAX(score) AS highest_score FROM quiz_scores WHERE quiz_id = ?",
        (quiz_id,),
    ).fetchone()["highest_score"]
    if highest_score is not None and highest_score > new_max_marks:
        raise HTTPException(
            status_code=422,
            detail=(
                f"max_marks cannot be below an existing score ({highest_score}). "
                "Update or remove that score first."
            ),
        )

    set_clause = ", ".join(f"{field} = ?" for field in updates)
    db.execute(
        f"UPDATE quizzes SET {set_clause} WHERE quiz_id = ?",
        [*updates.values(), quiz_id],
    )
    return dict(_get_quiz(db, quiz_id))


@router.delete("/{quiz_id}", status_code=204)
def delete_quiz(quiz_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    _get_quiz(db, quiz_id)
    try:
        db.execute("DELETE FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete quiz {quiz_id}: student scores exist. Delete the scores first.",
        )
    return None


@router.post("/{quiz_id}/scores", response_model=QuizScoreResponse, status_code=201)
def create_quiz_score(
    quiz_id: int,
    score: QuizScoreCreate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    quiz = _get_quiz(db, quiz_id)
    _ensure_active_student(db, score.student_id)
    _ensure_score_within_max(score.score, quiz["max_marks"])
    try:
        cursor = db.execute(
            """
            INSERT INTO quiz_scores (student_id, quiz_id, score, remarks)
            VALUES (?, ?, ?, ?)
            """,
            (score.student_id, quiz_id, score.score, score.remarks),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Student {score.student_id} already has a score for quiz {quiz_id}",
        )
    row = db.execute("SELECT * FROM quiz_scores WHERE score_id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@router.get("/{quiz_id}/scores", response_model=List[QuizScoreResponse])
def list_scores_for_quiz(
    quiz_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    _get_quiz(db, quiz_id)
    rows = db.execute(
        """
        SELECT * FROM quiz_scores WHERE quiz_id = ?
        ORDER BY score DESC, score_id ASC LIMIT ? OFFSET ?
        """,
        (quiz_id, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


@scores_router.get("", response_model=List[QuizScoreResponse])
def list_quiz_scores(
    student_id: Optional[int] = None,
    quiz_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    query = "SELECT * FROM quiz_scores WHERE 1=1"
    params = []
    if student_id is not None:
        query += " AND student_id = ?"
        params.append(student_id)
    if quiz_id is not None:
        query += " AND quiz_id = ?"
        params.append(quiz_id)
    query += " ORDER BY quiz_id DESC, score DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [dict(row) for row in db.execute(query, params).fetchall()]


def _get_score(db: sqlite3.Connection, score_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM quiz_scores WHERE score_id = ?", (score_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Quiz score {score_id} not found")
    return row


@scores_router.get("/{score_id}", response_model=QuizScoreResponse)
def get_quiz_score(score_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    return dict(_get_score(db, score_id))


@scores_router.patch("/{score_id}", response_model=QuizScoreResponse)
def update_quiz_score(
    score_id: int,
    score: QuizScoreUpdate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    existing = _get_score(db, score_id)
    updates = score.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")
    if "score" in updates and updates["score"] is None:
        raise HTTPException(status_code=422, detail="score cannot be null")
    if "score" in updates:
        quiz = _get_quiz(db, existing["quiz_id"])
        _ensure_score_within_max(updates["score"], quiz["max_marks"])

    set_clause = ", ".join(f"{field} = ?" for field in updates)
    db.execute(
        f"UPDATE quiz_scores SET {set_clause} WHERE score_id = ?",
        [*updates.values(), score_id],
    )
    return dict(_get_score(db, score_id))


@scores_router.delete("/{score_id}", status_code=204)
def delete_quiz_score(score_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    _get_score(db, score_id)
    db.execute("DELETE FROM quiz_scores WHERE score_id = ?", (score_id,))
    return None
