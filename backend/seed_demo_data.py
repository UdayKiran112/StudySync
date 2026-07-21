"""Create a realistic, repeatable demo dataset for every StudySync module.

This script only adds the rows that are missing, so it is safe to run again
against an existing library.db.  The generated records use the ``DEMO``
prefix (or student IDs 1001-1030) to make them easy to recognise.

Run from the backend folder:
    ..\\study_sync\\Scripts\\python.exe seed_demo_data.py
"""

from datetime import date, timedelta

from database import get_db


STUDENTS = [
    (1001, "Aarav Sharma", "Male"), (1002, "Ananya Iyer", "Female"),
    (1003, "Arjun Mehta", "Male"), (1004, "Diya Nair", "Female"),
    (1005, "Kabir Singh", "Male"), (1006, "Kavya Reddy", "Female"),
    (1007, "Rohan Gupta", "Male"), (1008, "Ishita Verma", "Female"),
    (1009, "Vivaan Joshi", "Male"), (1010, "Myra Kapoor", "Female"),
    (1011, "Aditya Rao", "Male"), (1012, "Saanvi Das", "Female"),
    (1013, "Reyansh Patel", "Male"), (1014, "Aadhya Menon", "Female"),
    (1015, "Vihaan Kulkarni", "Male"), (1016, "Anika Bansal", "Female"),
    (1017, "Dhruv Malhotra", "Male"), (1018, "Riya Chawla", "Female"),
    (1019, "Atharv Jain", "Male"), (1020, "Meera Sinha", "Female"),
    (1021, "Aryan Shah", "Male"), (1022, "Nisha Yadav", "Female"),
    (1023, "Samar Khanna", "Male"), (1024, "Pooja Arora", "Female"),
    (1025, "Kunal Bose", "Male"), (1026, "Tanya Mishra", "Female"),
    (1027, "Manav Bhat", "Male"), (1028, "Ira Saxena", "Female"),
    (1029, "Yash Desai", "Male"), (1030, "Neha Pillai", "Female"),
]

SUBSCRIPTIONS = [
    (f"DEMO-SUB-{n:02}", name, kind, 299 + n * 25, 30 + (n % 4) * 30)
    for n, (name, kind) in enumerate(
        [
            ("JSTOR Research", "Research"), ("Coursera Plus", "Online Learning"),
            ("Britannica Online", "Reference"), ("NPTEL Courses", "Online Learning"),
            ("The Hindu ePaper", "Newspaper"), ("Pearson Practice", "Test Prep"),
            ("Unacademy Library", "Test Prep"), ("Udemy Business", "Online Learning"),
            ("Oxford Reference", "Reference"), ("Swayam Learning", "Online Learning"),
            ("Economic Times", "Newspaper"), ("Khan Academy", "Online Learning"),
            ("Cambridge Core", "Research"), ("Skillshare", "Online Learning"),
            ("BYJU'S Exam Prep", "Test Prep"), ("Nature Archive", "Research"),
            ("India Today", "Magazine"), ("edX Access", "Online Learning"),
            ("Sage Journals", "Research"), ("Testbook Pass", "Test Prep"),
            ("Mint Premium", "Newspaper"), ("LinkedIn Learning", "Online Learning"),
            ("SpringerLink", "Research"), ("Adda247 Prime", "Test Prep"),
            ("Harvard Business Review", "Magazine"), ("Pluralsight Skills", "Online Learning"),
            ("ProQuest Central", "Research"), ("Oliveboard Edge", "Test Prep"),
            ("Frontline Digital", "Magazine"), ("Magoosh Practice", "Test Prep"),
        ], start=1
    )
]

BOOK_TITLES = [
    "Indian Polity", "Modern Indian History", "Quantitative Aptitude", "General Science",
    "Indian Economy", "Geography of India", "Logical Reasoning", "English Grammar",
    "Current Affairs Digest", "Environmental Studies", "Constitution at Work", "Data Interpretation",
    "World History", "Basic Computer Skills", "Ethics in Public Life", "Physics Fundamentals",
    "Chemistry Essentials", "Biology Made Simple", "Public Administration", "Indian Art and Culture",
    "Verbal Ability", "Number Systems", "Probability Basics", "Indian Geography Atlas",
    "Social Issues in India", "Science and Technology", "Daily Editorials", "Essay Writing Guide",
    "Time Management", "Mock Test Workbook",
]

SUBJECTS = ["Mathematics", "Science", "English", "Reasoning", "General Knowledge"]


def _ensure_seed_rows(db):
    base_day = date(2026, 6, 1)

    for index, (student_id, name, gender) in enumerate(STUDENTS):
        db.execute(
            """INSERT OR IGNORE INTO students
            (student_id, name, gender, date_of_birth, phone, email, address, join_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Active')""",
            (student_id, name, gender, date(2004 + index % 4, index % 12 + 1, index % 27 + 1),
             f"90000{index:05}", f"demo.{student_id}@studysync.test",
             f"{index + 1} Study Lane, Bengaluru", base_day - timedelta(days=180 + index)),
        )

    for subscription_id, name, kind, cost, validity_days in SUBSCRIPTIONS:
        db.execute(
            """INSERT OR IGNORE INTO subscriptions
            (subscription_id, name, type, cost, validity_days, status) VALUES (?, ?, ?, ?, ?, 'Active')""",
            (subscription_id, name, kind, cost, validity_days),
        )

    for index, title in enumerate(BOOK_TITLES, start=1):
        db.execute(
            """INSERT OR IGNORE INTO books (book_id, title, category, author, added_date)
            VALUES (?, ?, ?, ?, ?)""",
            (f"DEMO-BOOK-{index:02}", title, SUBJECTS[(index - 1) % len(SUBJECTS)],
             f"StudySync Press {index}", base_day - timedelta(days=index * 3)),
        )

    for index in range(30):
        student_id = STUDENTS[index][0]
        day = base_day + timedelta(days=index)
        check_in = "09:00" if index % 3 else "10:15"
        check_out = "12:30" if index % 3 else "15:30"
        session = "Morning" if index % 3 else "Full Day"
        duration = 210 if index % 3 else 255
        db.execute(
            """INSERT OR IGNORE INTO attendance
            (student_id, date, session, check_in, check_out, duration_minutes)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (student_id, day, session, check_in, check_out, duration),
        )
        db.execute(
            """INSERT OR IGNORE INTO digital_library_usage
            (student_id, date, in_time, out_time, account_type, subscription_id, platform_name, purpose, notes)
            VALUES (?, ?, '10:00', '11:30', 'Library Subscription', ?, ?, ?, ?)""",
            (student_id, day, SUBSCRIPTIONS[index][0], SUBSCRIPTIONS[index][1],
             SUBJECTS[index % len(SUBJECTS)], "Demo learning session"),
        )
        db.execute(
            """INSERT OR IGNORE INTO offline_library_usage (student_id, date, book_id)
            VALUES (?, ?, ?)""",
            (student_id, day, f"DEMO-BOOK-{index + 1:02}"),
        )

        exam_name = f"DEMO {SUBJECTS[index % len(SUBJECTS)]} Assessment {index + 1:02}"
        db.execute(
            "INSERT OR IGNORE INTO exams (exam_name, exam_date, subject, max_marks) VALUES (?, ?, ?, 100)",
            (exam_name, day, SUBJECTS[index % len(SUBJECTS)]),
        )
        exam_id = db.execute("SELECT exam_id FROM exams WHERE exam_name = ?", (exam_name,)).fetchone()[0]
        db.execute(
            "INSERT OR IGNORE INTO exam_marks (student_id, exam_id, marks_obtained, remarks) VALUES (?, ?, ?, ?)",
            (student_id, exam_id, 55 + (index * 7) % 41, "Demo assessment result"),
        )

        quiz_name = f"DEMO {SUBJECTS[(index + 2) % len(SUBJECTS)]} Quiz {index + 1:02}"
        db.execute(
            "INSERT OR IGNORE INTO quizzes (quiz_name, quiz_date, subject, max_marks) VALUES (?, ?, ?, 50)",
            (quiz_name, day + timedelta(days=1), SUBJECTS[(index + 2) % len(SUBJECTS)]),
        )
        quiz_id = db.execute("SELECT quiz_id FROM quizzes WHERE quiz_name = ?", (quiz_name,)).fetchone()[0]
        db.execute(
            "INSERT OR IGNORE INTO quiz_scores (student_id, quiz_id, score, remarks) VALUES (?, ?, ?, ?)",
            (student_id, quiz_id, 25 + (index * 3) % 24, "Demo quiz result"),
        )

    for index in range(30):
        detail = f"Demo sync record {index + 1:02}; no external sync was performed."
        db.execute("INSERT OR IGNORE INTO sync_log (synced_at, status, details) VALUES (?, 'Success', ?)",
                   (base_day + timedelta(days=index), detail))

    for index in range(30):
        class_date = base_day + timedelta(days=index * 2)
        title = f"DEMO Coaching Session {index + 1:02}"
        db.execute(
            """INSERT INTO coaching_classes
            (title, class_date, start_time, end_time, subject, instructor, venue, capacity, notes)
            SELECT ?, ?, '10:00', '12:00', ?, ?, 'StudySync Hall', 40, 'Demo coaching class'
            WHERE NOT EXISTS (SELECT 1 FROM coaching_classes WHERE title = ? AND class_date = ?)""",
            (title, class_date, SUBJECTS[index % len(SUBJECTS)], f"Instructor {index % 5 + 1}", title, class_date),
        )
        class_id = db.execute("SELECT class_id FROM coaching_classes WHERE title = ?", (title,)).fetchone()[0]
        student_id = STUDENTS[index][0]
        db.execute(
            """INSERT OR IGNORE INTO coaching_enrollments
            (class_id, participant_type, student_id, attendance_status)
            VALUES (?, 'Library Student', ?, 'Present')""",
            (class_id, student_id),
        )
        db.execute(
            """INSERT INTO coaching_enrollments
            (class_id, participant_type, external_name, village, phone, guardian_name, attendance_status)
            SELECT ?, 'External Student', ?, ?, ?, ?, 'Registered'
            WHERE NOT EXISTS (
                SELECT 1 FROM coaching_enrollments WHERE class_id = ? AND external_name = ?
            )""",
            (class_id, f"Demo Visitor {index + 1:02}", f"Village {index % 10 + 1}",
             f"80000{index:05}", f"Guardian {index + 1:02}", class_id, f"Demo Visitor {index + 1:02}"),
        )


def main():
    with get_db() as db:
        _ensure_seed_rows(db)
        tables = ["students", "subscriptions", "books", "attendance", "digital_library_usage",
                  "offline_library_usage", "exams", "exam_marks", "quizzes", "quiz_scores", "sync_log",
                  "coaching_classes", "coaching_enrollments"]
        counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
    print("Demo data is ready:")
    for table, count in counts.items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
