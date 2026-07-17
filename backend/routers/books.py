"""
routers/books.py
------------------
CRUD for the books catalog. Referenced by offline_library_usage.book_id.

Deleting a book that's already been read/referenced in
offline_library_usage will fail (ON DELETE RESTRICT) — there's no
status field to fall back on here (unlike students/subscriptions), so
if a book needs to be retired from circulation while preserving history,
the practical option is to leave the catalog entry in place and simply
stop referencing it in new offline_library_usage entries.
"""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional

from database import get_db_dependency
from models.books import BookCreate, BookUpdate, BookResponse
from security import require_api_key

router = APIRouter(
    prefix="/api/books", tags=["Books"], dependencies=[Depends(require_api_key)]
)


@router.post("", response_model=BookResponse, status_code=201)
def create_book(book: BookCreate, db: sqlite3.Connection = Depends(get_db_dependency)):
    """Add a new book to the catalog. 409 if the ID is already taken."""
    existing = db.execute(
        "SELECT book_id FROM books WHERE book_id = ?", (book.book_id,)
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=409, detail=f"Book ID '{book.book_id}' already exists"
        )

    db.execute(
        """
        INSERT INTO books (book_id, title, category, author, added_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (book.book_id, book.title, book.category, book.author, book.added_date),
    )

    row = db.execute(
        "SELECT * FROM books WHERE book_id = ?", (book.book_id,)
    ).fetchone()
    return dict(row)


@router.get("", response_model=List[BookResponse])
def list_books(
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """List all books, optionally filtered by category and/or title/author search."""
    query = "SELECT * FROM books WHERE 1=1"
    params = []

    if category:
        query += " AND category = ?"
        params.append(category)

    if search:
        query += " AND (title LIKE ? OR author LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    query += " ORDER BY title LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/{book_id}", response_model=BookResponse)
def get_book(book_id: str, db: sqlite3.Connection = Depends(get_db_dependency)):
    """Fetch a single book by ID."""
    row = db.execute("SELECT * FROM books WHERE book_id = ?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Book '{book_id}' not found")
    return dict(row)


@router.patch("/{book_id}", response_model=BookResponse)
def update_book(
    book_id: str, book: BookUpdate, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Partially update a book's catalog details. Only supplied fields are changed."""
    existing = db.execute(
        "SELECT * FROM books WHERE book_id = ?", (book_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Book '{book_id}' not found")

    updates = book.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    # `title` is nullable in the PATCH model so it can be omitted, but the
    # database column is NOT NULL. Reject an explicit null before SQLite
    # raises an unhandled integrity error.
    if "title" in updates and updates["title"] is None:
        raise HTTPException(status_code=422, detail="title cannot be null")

    set_clause = ", ".join(f"{field} = ?" for field in updates.keys())
    values = list(updates.values()) + [book_id]

    db.execute(f"UPDATE books SET {set_clause} WHERE book_id = ?", values)

    row = db.execute("SELECT * FROM books WHERE book_id = ?", (book_id,)).fetchone()
    return dict(row)


@router.delete("/{book_id}", status_code=204)
def delete_book(book_id: str, db: sqlite3.Connection = Depends(get_db_dependency)):
    """
    Delete a book from the catalog. Fails with 409 if any
    offline_library_usage records reference it (ON DELETE RESTRICT).
    """
    existing = db.execute(
        "SELECT * FROM books WHERE book_id = ?", (book_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Book '{book_id}' not found")

    try:
        db.execute("DELETE FROM books WHERE book_id = ?", (book_id,))
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete book '{book_id}': it has been referenced in "
                "offline library usage records."
            ),
        )
    return None
