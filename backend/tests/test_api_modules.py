"""Integration checks for every active StudySync API module.

The suite uses a disposable SQLite database; it never changes backend/library.db.
Run it from the project root with:
    & .\\study_sync\\Scripts\\python.exe -m unittest discover -s backend/tests -v
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
API_KEY = "test-key-0123456789abcdef0123456789abcdef"
os.environ["STUDYSYNC_API_KEY"] = API_KEY

import database  # noqa: E402


class ApiModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # SQLite's WAL files can remain momentarily locked on Windows after
        # the final request. Ignore that cleanup race; the OS temp directory
        # will remove any remaining file after this short-lived test process.
        cls.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        database.DB_PATH = Path(cls.temp_dir.name) / "test_library.db"
        schema = (BACKEND_DIR / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(database.DB_PATH) as connection:
            connection.executescript(schema)

        from main import app  # Import after DB_PATH has been redirected.

        # Keep the app lifespan explicit so its database work is shut down
        # before Windows removes the temporary SQLite files.
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.headers = {"X-API-Key": API_KEY}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.temp_dir.cleanup()

    def request(self, method, path, **kwargs):
        return self.client.request(method, path, headers=self.headers, **kwargs)

    def test_01_health(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "running")

    def test_02_students_module(self):
        payload = {
            "student_id": 9001, "name": "Test Student", "gender": "Female",
            "join_date": "2026-06-01", "email": "student@example.test",
        }
        self.assertEqual(self.request("POST", "/api/students", json=payload).status_code, 201)
        self.assertEqual(self.request("GET", "/api/students?search=Test").json()[0]["student_id"], 9001)
        self.assertEqual(self.request("PATCH", "/api/students/9001", json={"status": "Inactive"}).status_code, 200)
        self.assertEqual(self.request("PATCH", "/api/students/9001", json={"status": "Active"}).status_code, 200)

    def test_02b_student_summary_search_with_attendance(self):
        # Regression: /api/students/summary?search=... JOINs attendance and
        # used to crash with "ambiguous column name: student_id".
        self.assertEqual(self.request("GET", "/api/students/summary?search=Test").status_code, 200)

    def test_02c_zkteco_info_accepts_int_versions(self):
        from models.zkteco import ZkDeviceInfo

        info = ZkDeviceInfo(face_version=7, fp_version=7)
        self.assertEqual(info.face_version, "7")
        self.assertEqual(info.fp_version, "7")
        self.assertIsNone(ZkDeviceInfo().face_version)

    def test_03_books_and_subscriptions_modules(self):
        book = {"book_id": "TEST-BOOK", "title": "Testing Fundamentals", "category": "Science", "author": "A. Author", "added_date": "2026-06-01"}
        subscription = {"subscription_id": "TEST-SUB", "name": "Test Research", "type": "Research", "cost": 499, "validity_days": 30}
        self.assertEqual(self.request("POST", "/api/books", json=book).status_code, 201)
        self.assertEqual(self.request("GET", "/api/books?category=Science").status_code, 200)
        self.assertEqual(self.request("POST", "/api/subscriptions", json=subscription).status_code, 201)
        self.assertEqual(self.request("GET", "/api/subscriptions?status=Active").status_code, 200)

    def test_04_attendance_module(self):
        check_in = {"student_id": 9001, "date": "2026-06-02", "check_in": "09:30"}
        self.assertEqual(self.request("POST", "/api/attendance/check-in", json=check_in).status_code, 201)
        response = self.request("PATCH", "/api/attendance/check-out", json={"student_id": 9001, "check_out": "15:00"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["duration_minutes"], 270)
        self.assertEqual(self.request("GET", "/api/attendance?student_id=9001").status_code, 200)

    def test_04b_auto_renew_on_attendance(self):
        today = date.today().isoformat()
        # Joined two years before today, never renewed -> deeply expired and
        # imported as Inactive by the back-dated status rule.
        payload = {"student_id": 9002, "name": "Expired Student", "gender": "Male", "join_date": "2024-06-01"}
        self.assertEqual(self.request("POST", "/api/students", json=payload).status_code, 201)
        self.assertEqual(self.request("GET", "/api/students/9002").json()["status"], "Inactive")

        # Checking in must auto-renew (renewal_count bumps year-by-year until
        # valid), flip status back to Active, and still record attendance.
        self.assertEqual(
            self.request("POST", "/api/attendance/check-in", json={"student_id": 9002, "date": today, "check_in": "10:00"}).status_code,
            201,
        )
        after = self.request("GET", "/api/students/9002").json()
        self.assertEqual(after["status"], "Active")
        self.assertGreaterEqual(after["renewal_count"], 2)
        self.assertGreaterEqual(after["valid_until"], today)
        self.assertEqual(len(self.request("GET", "/api/attendance?student_id=9002").json()["items"]), 1)

        # A student who is already valid is left untouched (idempotent).
        self.assertEqual(self.request("GET", "/api/students/9001").json()["renewal_count"], 0)
        self.assertEqual(
            self.request("POST", "/api/attendance/check-in", json={"student_id": 9001, "date": today, "check_in": "09:00"}).status_code,
            201,
        )
        self.assertEqual(self.request("GET", "/api/students/9001").json()["renewal_count"], 0)

    def test_05_digital_library_module(self):
        payload = {"student_id": 9001, "date": "2026-06-03", "in_time": "10:00", "account_type": "Library Subscription", "subscription_id": "TEST-SUB", "platform_name": "Test Research", "purpose": "Revision"}
        self.assertEqual(self.request("POST", "/api/digital-library/check-in", json=payload).status_code, 201)
        response = self.request("POST", "/api/digital-library/check-out", json={"student_id": 9001, "out_time": "11:30"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["duration_minutes"], 90)

    def test_06_offline_library_module(self):
        response = self.request("POST", "/api/offline-library", json={"student_id": 9001, "date": "2026-06-03", "book_id": "TEST-BOOK"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.request("GET", "/api/offline-library?student_id=9001").status_code, 200)

    def test_07_exams_and_marks_modules(self):
        response = self.request("POST", "/api/exams", json={"exam_name": "Testing Exam", "exam_date": "2026-06-04", "subject": "Science", "max_marks": 100})
        self.assertEqual(response.status_code, 201)
        exam_id = response.json()["exam_id"]
        self.assertEqual(self.request("POST", f"/api/exams/{exam_id}/marks", json={"student_id": 9001, "marks_obtained": 85, "remarks": "Good"}).status_code, 201)
        self.assertEqual(self.request("GET", f"/api/exams/{exam_id}/marks").status_code, 200)
        self.assertEqual(self.request("GET", "/api/exam-marks?student_id=9001").status_code, 200)

    def test_08_quizzes_and_scores_modules(self):
        response = self.request("POST", "/api/quizzes", json={"quiz_name": "Testing Quiz", "quiz_date": "2026-06-05", "subject": "Reasoning", "max_marks": 50})
        self.assertEqual(response.status_code, 201)
        quiz_id = response.json()["quiz_id"]
        self.assertEqual(self.request("POST", f"/api/quizzes/{quiz_id}/scores", json={"student_id": 9001, "score": 42, "remarks": "Strong"}).status_code, 201)
        self.assertEqual(self.request("GET", f"/api/quizzes/{quiz_id}/scores").status_code, 200)
        self.assertEqual(self.request("GET", "/api/quiz-scores?student_id=9001").status_code, 200)

    def test_09_dashboard_module(self):
        response = self.request("GET", "/api/dashboard/students/9001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["student"]["student_id"], 9001)

    def test_09b_currently_present_lists_open_sessions_with_names(self):
        # 9001 has an open attendance session from test_04b; open a digital
        # library session too, then confirm the dashboard list joins both
        # with names.
        self.assertEqual(
            self.request(
                "POST", "/api/digital-library/check-in",
                json={"student_id": 9001, "date": "2026-06-07", "in_time": "10:15",
                      "account_type": "Library Subscription",
                      "subscription_id": "TEST-SUB", "platform_name": "Test Research"},
            ).status_code,
            201,
        )
        response = self.request("GET", "/api/dashboard/currently-present")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        ids = {(item["student_id"], item["activity"]) for item in body}
        self.assertIn((9001, "attendance"), ids)
        self.assertIn((9001, "digital_library"), ids)
        for item in body:
            self.assertIn("name", item)
            self.assertTrue(item["name"])

    def test_10_coaching_classes_module(self):
        instructor = self.request("POST", "/api/coaching-classes/instructors", json={"name": "Test Instructor"})
        self.assertEqual(instructor.status_code, 201)
        external = self.request("POST", "/api/coaching-classes/external-participants", json={"name": "Outside Visitor", "village": "Kolar", "phone": "9000012345"})
        self.assertEqual(external.status_code, 201)
        response = self.request("POST", "/api/coaching-classes", json={
            "title": "Study Skills Workshop", "class_date": "2026-06-06",
            "start_time": "10:00", "end_time": "12:00", "instructor_id": instructor.json()["instructor_id"],
        })
        self.assertEqual(response.status_code, 201)
        class_id = response.json()["class_id"]
        self.assertEqual(self.request("POST", f"/api/coaching-classes/{class_id}/enrollments", json={
            "participant_type": "Library Student", "student_id": 9001,
        }).status_code, 201)
        self.assertEqual(self.request("POST", f"/api/coaching-classes/{class_id}/enrollments", json={
            "participant_type": "External Student", "external_participant_id": external.json()["external_participant_id"],
        }).status_code, 201)
        roster = self.request("GET", f"/api/coaching-classes/{class_id}/enrollments")
        self.assertEqual(roster.status_code, 200)
        self.assertEqual({item["participant_type"] for item in roster.json()}, {"Library Student", "External Student"})

    # --- ZKTeco sync-report (PyZK) ----------------------------------------
    #
    # The old ADMS HTTP push transport was removed, so there is no
    # device-facing HTTP punch endpoint anymore; punch application is
    # covered directly in test_attendance_punch.py. The sync-report
    # endpoint is device-independent (it reads the ledger), so it is
    # exercised through the API here.

    def test_11_sync_report_endpoint(self):
        report = self.request("GET", "/api/zkteco/sync-report")
        self.assertEqual(report.status_code, 200)
        body = report.json()
        for key in ("ledger_total", "ledger_pending", "ledger_applied",
                    "ledger_duplicate_transport", "ledger_duplicate_debounced",
                    "ledger_duplicate_session", "ledger_unknown_student",
                    "fully_synced"):
            self.assertIn(key, body)
        self.assertEqual(body["ledger_total"], 0)
        self.assertTrue(body["fully_synced"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
