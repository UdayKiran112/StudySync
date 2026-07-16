PRAGMA foreign_keys = ON;

-- ===================================
-- STUDENTS
-- ===================================
CREATE TABLE students (
    student_id      INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    gender          TEXT CHECK(gender IN ('Male', 'Female', 'Other')),
    date_of_birth   DATE,
    phone           TEXT,
    email           TEXT,
    address         TEXT,
    join_date       DATE NOT NULL,
    photo_path      TEXT,
    status          TEXT DEFAULT 'Active' CHECK(status IN ('Active', 'Inactive')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    session         TEXT NOT NULL CHECK(session IN ('Morning', 'Afternoon')),
    check_in        TEXT,                  -- 'HH:MM' 24-hour
    check_out       TEXT,
    duration_minutes INTEGER GENERATED ALWAYS AS (
        CASE
            WHEN check_in IS NOT NULL AND check_out IS NOT NULL THEN
                CAST(ROUND(
                    (julianday('2000-01-01 ' || check_out) - julianday('2000-01-01 ' || check_in)) * 24 * 60
                ) AS INTEGER)
            ELSE NULL
        END
    ) STORED,
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
    subscription_id TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    type            TEXT,
    cost            REAL,
    validity_days   INTEGER,
    status          TEXT DEFAULT 'Active' CHECK(status IN ('Active', 'Expired'))
);

-- ===================================
-- DIGITAL LIBRARY USAGE
-- ===================================
CREATE TABLE digital_library_usage (
    usage_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id       INTEGER NOT NULL,
    date             DATE NOT NULL,

    in_time          TEXT ,      -- HH:MM (24-hour)
    out_time         TEXT ,

    duration_minutes INTEGER GENERATED ALWAYS AS (
        CAST(ROUND(
            (julianday('2000-01-01 ' || out_time) -
             julianday('2000-01-01 ' || in_time)) * 24 * 60
        ) AS INTEGER)
    ) STORED,

    account_type     TEXT NOT NULL
                     CHECK(account_type IN ('Library Subscription', 'Own Account')),

    subscription_id  TEXT,

    platform_name    TEXT NOT NULL,

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
    book_id         TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
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

-- ===================================
-- EXAMS
-- ===================================
CREATE TABLE exams (
    exam_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_name       TEXT NOT NULL,
    exam_date       DATE,
    subject         TEXT,
    max_marks       REAL NOT NULL
);

CREATE INDEX idx_exams_exam_date ON exams(exam_date);

CREATE TABLE exam_marks (
    mark_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    exam_id         INTEGER NOT NULL,
    marks_obtained  REAL NOT NULL,
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
    quiz_name       TEXT NOT NULL,
    quiz_date       DATE,
    subject         TEXT,
    max_marks       REAL NOT NULL
);

CREATE INDEX idx_quizzes_quiz_date ON quizzes(quiz_date);

CREATE TABLE quiz_scores (
    score_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL,
    quiz_id         INTEGER NOT NULL,
    score           REAL NOT NULL,
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
    synced_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status          TEXT CHECK(status IN ('Success', 'Failed', 'Partial')),
    details         TEXT
);