import sqlite3
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from database import get_db_dependency
from models.other_activities import *
from security import require_api_key

router = APIRouter(prefix='/api/other-activities', tags=['Other Activities'], dependencies=[Depends(require_api_key)])

def activity_row(db, id):
    row = db.execute('SELECT * FROM other_activities WHERE activity_id=?', (id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Activity not found')
    return row

def attendance_row(db, id):
    row = db.execute("""SELECT a.*, COALESCE(s.name, p.name) participant_name, p.village, p.phone 
    FROM other_activities_attendance a
    LEFT JOIN students s ON s.student_id=a.student_id 
    LEFT JOIN external_participants p ON p.external_participant_id=a.external_participant_id 
    WHERE a.attendance_id=?""", (id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Attendance record not found')
    return row

@router.post('', response_model=OtherActivityResponse, status_code=201)
def add_activity(p: OtherActivityCreate, db: sqlite3.Connection = Depends(get_db_dependency)):
    c = db.execute('INSERT INTO other_activities(session_name, speaker_name, session_date, session_type, notes) VALUES(?,?,?,?,?)',
                   (p.session_name, p.speaker_name, p.session_date, p.session_type, p.notes))
    return dict(activity_row(db, c.lastrowid))

@router.get('', response_model=List[OtherActivityResponse])
def get_activities(date_: Optional[date] = None, db: sqlite3.Connection = Depends(get_db_dependency)):
    q = 'SELECT * FROM other_activities'
    args = []
    if date_:
        q += ' WHERE session_date=?'
        args = [date_]
    return [dict(x) for x in db.execute(q + ' ORDER BY session_date DESC, activity_id DESC', args).fetchall()]

@router.get('/{activity_id}', response_model=OtherActivityResponse)
def get_activity(activity_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    return dict(activity_row(db, activity_id))

@router.put('/{activity_id}', response_model=OtherActivityResponse)
def update_activity(activity_id: int, p: OtherActivityUpdate, db: sqlite3.Connection = Depends(get_db_dependency)):
    activity_row(db, activity_id)
    db.execute('UPDATE other_activities SET session_name=?, speaker_name=?, session_date=?, session_type=?, notes=? WHERE activity_id=?',
               (p.session_name, p.speaker_name, p.session_date, p.session_type, p.notes, activity_id))
    return dict(activity_row(db, activity_id))

@router.delete('/{activity_id}', status_code=204)
def delete_activity(activity_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    activity_row(db, activity_id)
    db.execute('DELETE FROM other_activities WHERE activity_id=?', (activity_id,))

@router.get('/{activity_id}/attendance', response_model=List[OtherActivityAttendanceResponse])
def get_attendance(activity_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    activity_row(db, activity_id)
    return [dict(x) for x in db.execute("""SELECT a.*, COALESCE(s.name, p.name) participant_name, p.village, p.phone 
    FROM other_activities_attendance a
    LEFT JOIN students s ON s.student_id=a.student_id 
    LEFT JOIN external_participants p ON p.external_participant_id=a.external_participant_id 
    WHERE a.activity_id=? ORDER BY participant_name""", (activity_id,)).fetchall()]

@router.post('/{activity_id}/attendance', response_model=OtherActivityAttendanceResponse, status_code=201)
def add_attendance(activity_id: int, p: OtherActivityAttendanceCreate, db: sqlite3.Connection = Depends(get_db_dependency)):
    activity_data = activity_row(db, activity_id)
    c = db.execute('INSERT INTO other_activities_attendance(activity_id, participant_type, student_id, external_participant_id) VALUES(?,?,?,?)',
                   (activity_id, p.participant_type, p.student_id, p.external_participant_id))
    
    # Clean up auto-created self-study offline records if this is a library student
    if p.student_id:
        try:
            from routers.attendance import _cleanup_auto_filled_offline_if_needed
            _cleanup_auto_filled_offline_if_needed(db, p.student_id, activity_data['session_date'])
        except Exception:
            pass  # Cleanup is a side effect; don't let failures block attendance
    
    return dict(attendance_row(db, c.lastrowid))

@router.delete('/{activity_id}/attendance/{attendance_id}', status_code=204)
def delete_attendance(activity_id: int, attendance_id: int, db: sqlite3.Connection = Depends(get_db_dependency)):
    row = attendance_row(db, attendance_id)
    if row['activity_id'] != activity_id:
        raise HTTPException(404, 'Attendance record not found')
    db.execute('DELETE FROM other_activities_attendance WHERE attendance_id=?', (attendance_id,))
