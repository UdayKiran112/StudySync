"""Unit tests for sheets_client.write_sheet chunking (no real network).

Run from the project root with:
    & .\\study_sync\\Scripts\\python.exe -m unittest discover -s backend/tests -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import gspread

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import sheets_client  # noqa: E402


class _FakeWorksheet:
    def __init__(self):
        self.cleared = False
        self.calls = []  # (range_name, values)

    def clear(self):
        self.cleared = True

    def update(self, range_name=None, values=None, **kwargs):
        self.calls.append((range_name, values))


class _FakeSpreadsheet:
    def __init__(self, sheets):
        self.sheets = sheets

    def worksheet(self, name):
        if name not in self.sheets:
            raise gspread.exceptions.WorksheetNotFound(name)
        return self.sheets[name]

    def add_worksheet(self, title, rows, cols):
        ws = _FakeWorksheet()
        self.sheets[title] = ws
        return ws


class ChunkedWriteTests(unittest.TestCase):
    def setUp(self):
        os.environ["STUDYSYNC_SHEETS_MAX_CELLS_PER_REQUEST"] = "10"
        self.sheets = {}
        fake_gc = mock.Mock()
        fake_gc.open_by_key.return_value = _FakeSpreadsheet(self.sheets)
        self.patchers = [
            mock.patch.object(
                sheets_client,
                "_get_credentials",
                return_value=(mock.Mock(), "SHEET_ID"),
            ),
            mock.patch.object(gspread, "authorize", return_value=fake_gc),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        os.environ.pop("STUDYSYNC_SHEETS_MAX_CELLS_PER_REQUEST", None)

    def _worksheet(self):
        return self.sheets["Attendance"]

    def test_small_data_is_a_single_write(self):
        headers = ["Date", "Student ID"]
        data = [["2026-01-01", 1], ["2026-01-02", 2]]
        written = sheets_client.write_sheet("Attendance", headers, data)

        self.assertEqual(written, 2)
        ws = self._worksheet()  # worksheet did not exist -> created on demand
        self.assertFalse(ws.cleared)
        self.assertEqual(len(ws.calls), 1)
        self.assertEqual(ws.calls[0][0], "A1")
        self.assertEqual(ws.calls[0][1], [headers] + data)

    def test_large_data_is_split_across_chunked_writes(self):
        # Chunk limit of 10 cells with 5 columns -> 2 rows per API call.
        headers = ["A", "B", "C", "D", "E"]
        data = [[f"r{i}-{c}" for c in range(5)] for i in range(7)]
        written = sheets_client.write_sheet("Attendance", headers, data)

        self.assertEqual(written, 7)
        ws = self._worksheet()

        ranges = [rng for rng, _ in ws.calls]
        self.assertEqual(ranges, ["A1", "A3", "A5", "A7"])

        all_rows = [row for _, values in ws.calls for row in values]
        self.assertEqual(all_rows, [headers] + data)
        # Every chunk is at most the per-request size.
        for _, values in ws.calls:
            self.assertLessEqual(len(values) * len(headers), 10)

    def test_existing_worksheet_is_cleared_before_rewrite(self):
        # Pre-seed the sheet so it takes the clear-then-rewrite path.
        self.sheets["Attendance"] = _FakeWorksheet()
        data = [["2026-01-01", 1]]
        sheets_client.write_sheet("Attendance", ["Date", "Student ID"], data)

        ws = self._worksheet()
        self.assertTrue(ws.cleared)
        self.assertEqual(len(ws.calls), 1)

    def test_rows_preserved_in_order_across_chunks(self):
        headers = ["OnlyCol"]
        data = [[f"row{i}"] for i in range(25)]  # 26 rows, 1 col -> 26 calls
        sheets_client.write_sheet("Attendance", headers, data)

        ws = self._worksheet()
        flat = [row for _, values in ws.calls for row in values]
        self.assertEqual(flat, [headers] + data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
