PRAGMA foreign_keys = ON;

-- ===================================
-- STUDENTS
-- ===================================
CREATE TABLE students (
    student_id      INTEGER PRIMARY KEY,
    name            TEXT NOT NULL CHECK(length(trim(name)) > 0),
    gender          TEXT CHECK(gender IN ('Male', 'Female', 'Other')),
    date_of_birth   DATE,
    phone           TEXT,
    email           TEXT,
    father_name     TEXT,
    qualification   TEXT,
    goal            TEXT,
    preparing_for   TEXT,
    address         TEXT,
    join_date       DATE NOT NULL,
    photo_path      TEXT,
    status          TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Inactive')),
    -- Number of times membership has been renewed. join_date NEVER
    -- changes after creation -- membership validity is always computed
    -- as join_date + (renewal_count + 1) whole years, so it stays
    -- anchored to the original join date's calendar day no matter how
    -- many times (or when) the student renews.
    renewal_count   INTEGER NOT NULL DEFAULT 0 CHECK(renewal_count >= 0),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER trg_students_updated_at
AFTER UPDATE ON students
FOR EACH ROW
BEGIN
    UPDATE students SET updated_at = CURRENT_TIMESTAMP WHERE student_id = OLD.student_id;
END;

-- ===================================
-- ATTENDANCE
-- ===================================
CREATE TABLE attendance (
    attendance_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    date            DATE NOT NULL,
    session         TEXT NOT NULL CHECK(session IN ('Morning', 'Afternoon', 'Full Day')),
    check_in        TEXT CHECK(check_in IS NULL OR (check_in GLOB '[0-2][0-9]:[0-5][0-9]' AND substr(check_in, 1, 2) <= '23')),
    check_out       TEXT CHECK(check_out IS NULL OR (check_out GLOB '[0-2][0-9]:[0-5][0-9]' AND substr(check_out, 1, 2) <= '23')),
    -- No longer GENERATED: duration must account for the 1-2 PM lunch
    -- exclusion and Morning->Full Day reclassification, which is
    -- conditional application logic, not a pure SQL expression of
    -- check_in/check_out alone. Computed and written by the app at
    -- check-out time (and at manual correction).
    duration_minutes INTEGER CHECK(duration_minutes IS NULL OR duration_minutes >= 0),
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT,
    UNIQUE(student_id, date, session),
    CHECK (check_out IS NULL OR check_in IS NULL OR check_out > check_in)
);

CREATE INDEX idx_attendance_student_id ON attendance(student_id);
CREATE INDEX idx_attendance_date ON attendance(date);

-- ===================================
-- SUBSCRIPTIONS
-- ===================================
CREATE TABLE subscriptions (
    subscription_id TEXT PRIMARY KEY CHECK(length(trim(subscription_id)) > 0),
    name            TEXT NOT NULL CHECK(length(trim(name)) > 0),
    type            TEXT,
    cost            REAL CHECK(cost IS NULL OR cost >= 0),
    -- When this subscription was purchased/activated. Mirrors
    -- students.join_date: a permanent historical fact that renewal
    -- logic must never overwrite. NULLable: staff may add a
    -- subscription without knowing its start date yet.
    start_date      DATE,
    validity_days   INTEGER CHECK(validity_days IS NULL OR validity_days > 0),
    status          TEXT DEFAULT 'Active' CHECK(status IN ('Active', 'Expired'))
);

-- ===================================
-- DIGITAL LIBRARY USAGE
-- ===================================
CREATE TABLE digital_library_usage (
    usage_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id       INTEGER NOT NULL,
    date             DATE NOT NULL,

    in_time          TEXT NOT NULL CHECK(in_time GLOB '[0-2][0-9]:[0-5][0-9]' AND substr(in_time, 1, 2) <= '23'),
    out_time         TEXT CHECK(out_time IS NULL OR (out_time GLOB '[0-2][0-9]:[0-5][0-9]' AND substr(out_time, 1, 2) <= '23')),

    duration_minutes INTEGER GENERATED ALWAYS AS (
        CAST(ROUND(
            (julianday('2000-01-01 ' || out_time) -
             julianday('2000-01-01 ' || in_time)) * 24 * 60
        ) AS INTEGER)
    ) STORED,

    account_type     TEXT NOT NULL
                     CHECK(account_type IN ('Library Subscription', 'Own Account')),

    subscription_id  TEXT,

    platform_name    TEXT NOT NULL CHECK(length(trim(platform_name)) > 0),

    purpose          TEXT,
    notes            TEXT,

    FOREIGN KEY (student_id)
        REFERENCES students(student_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (subscription_id)
        REFERENCES subscriptions(subscription_id)
        ON DELETE RESTRICT,

    CHECK (
        (
            account_type = 'Library Subscription'
            AND subscription_id IS NOT NULL
        )
        OR
        (
            account_type = 'Own Account'
            AND subscription_id IS NULL
        )
    ),

    CHECK (out_time > in_time)
);

CREATE INDEX idx_digital_usage_student_id
ON digital_library_usage(student_id);

CREATE INDEX idx_digital_usage_date
ON digital_library_usage(date);

CREATE INDEX idx_digital_usage_subscription_id
ON digital_library_usage(subscription_id);

-- ===================================
-- BOOKS
-- ===================================
CREATE TABLE books (
    book_id         TEXT PRIMARY KEY CHECK(length(trim(book_id)) > 0),
    title           TEXT NOT NULL CHECK(length(trim(title)) > 0),
    category        TEXT,
    author          TEXT,
    added_date      DATE
);

-- ===================================
-- OFFLINE LIBRARY USAGE (no duration tracking)
-- ===================================
CREATE TABLE offline_library_usage (
    usage_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    date            DATE NOT NULL,
    book_id         TEXT,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT,
    FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE RESTRICT
);

CREATE INDEX idx_offline_usage_student_id ON offline_library_usage(student_id);
CREATE INDEX idx_offline_usage_date ON offline_library_usage(date);
CREATE INDEX idx_offline_usage_book_id ON offline_library_usage(book_id);

CREATE UNIQUE INDEX idx_attendance_one_open_session
ON attendance(student_id) WHERE check_out IS NULL;

CREATE UNIQUE INDEX idx_digital_one_open_session
ON digital_library_usage(student_id) WHERE out_time IS NULL;

-- ===================================
-- EXAMS
-- ===================================
CREATE TABLE exams (
    exam_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_name       TEXT NOT NULL CHECK(length(trim(exam_name)) > 0),
    exam_date       DATE,
    subject         TEXT,
    -- Nullable: exams are created from the marks register, which may not
    -- always supply a numeric max_marks (some register rows leave the Max
    -- Marks column blank and no value can be recovered).
    max_marks       REAL CHECK(max_marks IS NULL OR max_marks > 0)
);

CREATE INDEX idx_exams_exam_date ON exams(exam_date);

CREATE TABLE exam_marks (
    mark_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    exam_id         INTEGER NOT NULL,
    -- NOT NULL: a row only exists when a real score is known. The marks
    -- register is the sole source of scores (load_exam_marks.py). An
    -- offline-exam sitting without a score is never inserted -- it is
    -- flagged for manual review instead (see load_offline_exam.py).
    marks_obtained  REAL NOT NULL CHECK(marks_obtained >= 0),
    remarks         TEXT,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT,
    FOREIGN KEY (exam_id) REFERENCES exams(exam_id) ON DELETE RESTRICT,
    UNIQUE(student_id, exam_id)
);

CREATE INDEX idx_exam_marks_student_id ON exam_marks(student_id);
CREATE INDEX idx_exam_marks_exam_id ON exam_marks(exam_id);

-- ===================================
-- QUIZZES
-- ===================================
CREATE TABLE quizzes (
    quiz_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_name       TEXT NOT NULL CHECK(length(trim(quiz_name)) > 0),
    quiz_date       DATE,
    subject         TEXT,
    -- Nullable -- same reason as exams.max_marks above.
    max_marks       REAL CHECK(max_marks IS NULL OR max_marks > 0)
);

CREATE INDEX idx_quizzes_quiz_date ON quizzes(quiz_date);

CREATE TABLE quiz_scores (
    score_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    quiz_id         INTEGER NOT NULL,
    -- Nullable -- same reason as exam_marks.marks_obtained above.
    score           REAL CHECK(score IS NULL OR score >= 0),
    remarks         TEXT,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT,
    FOREIGN KEY (quiz_id) REFERENCES quizzes(quiz_id) ON DELETE RESTRICT,
    UNIQUE(student_id, quiz_id)
);

CREATE INDEX idx_quiz_scores_student_id ON quiz_scores(student_id);
CREATE INDEX idx_quiz_scores_quiz_id ON quiz_scores(quiz_id);

-- ===================================
-- SYNC LOG
-- ===================================
CREATE TABLE sync_log (
    sync_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status          TEXT CHECK(status IN ('Success', 'Failed', 'Partial')),
    details         TEXT
);

-- ===================================
-- COACHING CLASSES
-- ===================================
-- An enrolment deliberately has either a library student ID OR external
-- participant details. This keeps outside visitors out of the students table
-- while still allowing one coaching roster to show both groups together.
CREATE TABLE instructors (
    instructor_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL CHECK(length(trim(name)) > 0),
    phone           TEXT,
    specialization  TEXT,
    notes           TEXT,
    status          TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Inactive')),
    UNIQUE(name, phone)
);

CREATE TABLE external_participants (
    external_participant_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL CHECK(length(trim(name)) > 0),
    village         TEXT,
    phone           TEXT,
    gender          TEXT CHECK(gender IS NULL OR gender IN ('Male', 'Female', 'Other')),
    guardian_name   TEXT,
    notes           TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, phone)
);

CREATE TABLE coaching_classes (
    class_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL CHECK(length(trim(title)) > 0),
    class_date      DATE NOT NULL,
    start_time      TEXT CHECK(start_time IS NULL OR (start_time GLOB '[0-2][0-9]:[0-5][0-9]' AND substr(start_time, 1, 2) <= '23')),
    end_time        TEXT CHECK(end_time IS NULL OR (end_time GLOB '[0-2][0-9]:[0-5][0-9]' AND substr(end_time, 1, 2) <= '23')),
    duration_minutes INTEGER GENERATED ALWAYS AS (CASE WHEN start_time IS NOT NULL AND end_time IS NOT NULL THEN CAST(ROUND((julianday('2000-01-01 ' || end_time) - julianday('2000-01-01 ' || start_time)) * 24 * 60) AS INTEGER) END) STORED,
    subject         TEXT,
    instructor_id   INTEGER,
    notes           TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(end_time IS NULL OR start_time IS NULL OR end_time > start_time),
    FOREIGN KEY (instructor_id) REFERENCES instructors(instructor_id) ON DELETE SET NULL
);

CREATE TABLE coaching_enrollments (
    enrollment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id        INTEGER NOT NULL,
    participant_type TEXT NOT NULL CHECK(participant_type IN ('Library Student', 'External Student')),
    student_id      INTEGER,
    external_participant_id INTEGER,
    enrolled_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (class_id) REFERENCES coaching_classes(class_id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT,
    FOREIGN KEY (external_participant_id) REFERENCES external_participants(external_participant_id) ON DELETE RESTRICT,
    CHECK(
        (participant_type = 'Library Student' AND student_id IS NOT NULL AND external_participant_id IS NULL)
        OR
        (participant_type = 'External Student' AND student_id IS NULL AND external_participant_id IS NOT NULL)
    ),
    UNIQUE(class_id, student_id)
);

CREATE INDEX idx_coaching_classes_date ON coaching_classes(class_date);
CREATE INDEX idx_coaching_enrollments_class ON coaching_enrollments(class_id);
CREATE INDEX idx_coaching_enrollments_student ON coaching_enrollments(student_id);
CREATE INDEX idx_coaching_enrollments_external ON coaching_enrollments(external_participant_id);

-- ===================================
-- OTHER ACTIVITIES (Speaker/Faculty Sessions)
-- ===================================
CREATE TABLE other_activities (
    activity_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name    TEXT NOT NULL CHECK(length(trim(session_name)) > 0),
    speaker_name    TEXT NOT NULL CHECK(length(trim(speaker_name)) > 0),
    session_date    DATE NOT NULL,
    session_type    TEXT NOT NULL CHECK(length(trim(session_type)) > 0),
    notes           TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE other_activities_attendance (
    attendance_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id     INTEGER NOT NULL,
    participant_type TEXT NOT NULL CHECK(participant_type IN ('Library Student', 'External Student')),
    student_id      INTEGER,
    external_participant_id INTEGER,
    attended_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (activity_id) REFERENCES other_activities(activity_id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT,
    FOREIGN KEY (external_participant_id) REFERENCES external_participants(external_participant_id) ON DELETE RESTRICT,
    CHECK(
        (participant_type = 'Library Student' AND student_id IS NOT NULL AND external_participant_id IS NULL)
        OR
        (participant_type = 'External Student' AND student_id IS NULL AND external_participant_id IS NOT NULL)
    ),
    UNIQUE(activity_id, student_id, external_participant_id)
);

CREATE INDEX idx_other_activities_date ON other_activities(session_date);
CREATE INDEX idx_other_activities_attendance_activity ON other_activities_attendance(activity_id);
CREATE INDEX idx_other_activities_attendance_student ON other_activities_attendance(student_id);
CREATE INDEX idx_other_activities_attendance_external ON other_activities_attendance(external_participant_id);

-- ===================================
-- DEVICE PUNCH LEDGER (raw audit trail)
-- ===================================
-- One row per PHYSICAL punch from the ZKTeco device, captured exactly once
-- no matter how many times it is delivered (pyzk poll, pyzk live_capture,
-- or the reconciliation loop). `fingerprint` uniquely identifies a physical
-- punch across ALL transports, so a punch that both the poll and the live
-- stream deliver is claimed once here and applied to `attendance` exactly
-- once.
--
-- `state` records what the punch turned into:
--   pending              - captured but not yet applied (recovery point)
--   applied              - opened or closed an attendance session
--   duplicate_transport  - same fingerprint already captured by another
--                          transport / a device retry; nothing applied
--   duplicate_debounced  - genuine re-tap within the debounce window
--                          (default 1 min); never a check-out / new session
--   duplicate_session    - re-read of an already-applied punch or an
--                          out-of-order punch; nothing applied
--   unknown_student      - device PIN matches no students.student_id
--
-- The raw record is preserved verbatim for auditing even when the punch is
-- judged a duplicate and must NOT affect the student's real attendance.
CREATE TABLE device_punches (
    punch_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint    TEXT NOT NULL UNIQUE,
    device_serial  TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    student_id     INTEGER,
    punch_time     TEXT NOT NULL,          -- device clock, full seconds
    status_code    TEXT NOT NULL DEFAULT '',
    verify_method  TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL,          -- pyzk_poll | pyzk_live | reconcile
    state          TEXT NOT NULL DEFAULT 'pending'
                   CHECK(state IN ('pending', 'applied', 'duplicate_transport',
                                   'duplicate_debounced', 'duplicate_session',
                                   'unknown_student')),
    raw_record     TEXT,
    captured_at    TEXT NOT NULL,
    applied_at     TEXT,
    FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT
);

CREATE INDEX idx_device_punches_punch_time ON device_punches(punch_time);
CREATE INDEX idx_device_punches_student ON device_punches(student_id);
CREATE INDEX idx_device_punches_state ON device_punches(state);

-- ===================================
-- DEVICE STATE (persisted sync health)
-- ===================================
-- Survives restarts so operators can see when a device was last reconciled,
-- how big its buffer was, and how much of the ledger is still pending --
-- the in-memory zkteco live status (which resets on restart) is only a
-- liveness view; this is the durable record.
CREATE TABLE device_state (
    device_serial     TEXT PRIMARY KEY,
    last_seen_at      TEXT,
    last_reconcile_at TEXT,
    last_buffer_count INTEGER,
    ledger_pending    INTEGER,
    last_result       TEXT,
    buffer_capacity   INTEGER,
    buffer_status     TEXT,
    oldest_buffer_ts  TEXT,
    last_archive_path TEXT,
    last_archive_count INTEGER,
    last_clear_at     TEXT,
    clear_failures    INTEGER
);
