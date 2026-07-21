import sqlite3
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from database import get_db_dependency
from models.coaching import *
from security import require_api_key
router = APIRouter(prefix='/api/coaching-classes', tags=['Coaching Classes'], dependencies=[Depends(require_api_key)])

def class_row(db, id):
    row=db.execute('SELECT c.*, i.name instructor_name FROM coaching_classes c LEFT JOIN instructors i ON i.instructor_id=c.instructor_id WHERE c.class_id=?',(id,)).fetchone()
    if not row: raise HTTPException(404,'Coaching class not found')
    return row
def roster_row(db,id):
    row=db.execute("""SELECT e.*, COALESCE(s.name,p.name) participant_name, p.village, p.phone FROM coaching_enrollments e
    LEFT JOIN students s ON s.student_id=e.student_id LEFT JOIN external_participants p ON p.external_participant_id=e.external_participant_id WHERE e.enrollment_id=?""",(id,)).fetchone()
    if not row: raise HTTPException(404,'Enrollment not found')
    return row
@router.get('/instructors',response_model=List[InstructorResponse])
def instructors(db:sqlite3.Connection=Depends(get_db_dependency)): return [dict(x) for x in db.execute("SELECT * FROM instructors WHERE status='Active' ORDER BY name").fetchall()]
@router.post('/instructors',response_model=InstructorResponse,status_code=201)
def add_instructor(p:InstructorCreate,db:sqlite3.Connection=Depends(get_db_dependency)):
    try: c=db.execute('INSERT INTO instructors(name,phone,specialization,notes) VALUES(?,?,?,?)',tuple(p.model_dump().values()))
    except sqlite3.IntegrityError as e: raise HTTPException(409,'Instructor already exists') from e
    return dict(db.execute('SELECT * FROM instructors WHERE instructor_id=?',(c.lastrowid,)).fetchone())
@router.get('/external-participants',response_model=List[ExternalParticipantResponse])
def external_participants(search:Optional[str]=None,db:sqlite3.Connection=Depends(get_db_dependency)):
    q='SELECT * FROM external_participants WHERE 1=1'; args=[]
    if search: q+=' AND (name LIKE ? OR phone LIKE ? OR village LIKE ?)'; args += [f'%{search}%']*3
    return [dict(x) for x in db.execute(q+' ORDER BY name LIMIT 50',args).fetchall()]
@router.post('/external-participants',response_model=ExternalParticipantResponse,status_code=201)
def add_external(p:ExternalParticipantCreate,db:sqlite3.Connection=Depends(get_db_dependency)):
    try: c=db.execute('INSERT INTO external_participants(name,village,phone,gender,guardian_name,notes) VALUES(?,?,?,?,?,?)',tuple(p.model_dump().values()))
    except sqlite3.IntegrityError as e: raise HTTPException(409,'External participant already exists') from e
    return dict(db.execute('SELECT * FROM external_participants WHERE external_participant_id=?',(c.lastrowid,)).fetchone())
@router.post('',response_model=CoachingClassResponse,status_code=201)
def add_class(p:CoachingClassCreate,db:sqlite3.Connection=Depends(get_db_dependency)):
    if p.instructor_id and not db.execute('SELECT 1 FROM instructors WHERE instructor_id=?',(p.instructor_id,)).fetchone(): raise HTTPException(404,'Instructor not found')
    c=db.execute('INSERT INTO coaching_classes(title,class_date,start_time,end_time,subject,instructor_id,notes) VALUES(?,?,?,?,?,?,?)',tuple(p.model_dump().values())); return dict(class_row(db,c.lastrowid))
@router.get('',response_model=List[CoachingClassResponse])
def classes(date_:Optional[date]=None,db:sqlite3.Connection=Depends(get_db_dependency)):
    q='SELECT c.*,i.name instructor_name FROM coaching_classes c LEFT JOIN instructors i ON i.instructor_id=c.instructor_id'; args=[]
    if date_: q+=' WHERE c.class_date=?';args=[date_]
    return [dict(x) for x in db.execute(q+' ORDER BY c.class_date DESC,c.class_id DESC',args).fetchall()]
@router.get('/{class_id}',response_model=CoachingClassResponse)
def get_class(class_id:int,db:sqlite3.Connection=Depends(get_db_dependency)): return dict(class_row(db,class_id))
@router.delete('/{class_id}',status_code=204)
def delete_class(class_id:int,db:sqlite3.Connection=Depends(get_db_dependency)): class_row(db,class_id);db.execute('DELETE FROM coaching_classes WHERE class_id=?',(class_id,))
@router.get('/{class_id}/enrollments',response_model=List[CoachingEnrollmentResponse])
def roster(class_id:int,db:sqlite3.Connection=Depends(get_db_dependency)):
    class_row(db,class_id);return [dict(x) for x in db.execute("""SELECT e.*,COALESCE(s.name,p.name) participant_name,p.village,p.phone FROM coaching_enrollments e LEFT JOIN students s ON s.student_id=e.student_id LEFT JOIN external_participants p ON p.external_participant_id=e.external_participant_id WHERE e.class_id=? ORDER BY participant_name""",(class_id,)).fetchall()]
@router.post('/{class_id}/enrollments',response_model=CoachingEnrollmentResponse,status_code=201)
def enroll(class_id:int,p:CoachingEnrollmentCreate,db:sqlite3.Connection=Depends(get_db_dependency)):
    class_row_data = class_row(db,class_id)
    c=db.execute('INSERT INTO coaching_enrollments(class_id,participant_type,student_id,external_participant_id) VALUES(?,?,?,?)',(class_id,*p.model_dump().values()))
    
    # Clean up auto-created self-study offline records if this is a library student
    if p.student_id:
        try:
            from routers.attendance import _cleanup_auto_filled_offline_if_needed
            _cleanup_auto_filled_offline_if_needed(db, p.student_id, class_row_data['class_date'])
        except Exception:
            pass  # Cleanup is a side effect; don't let failures block enrollment
    
    return dict(roster_row(db,c.lastrowid))
