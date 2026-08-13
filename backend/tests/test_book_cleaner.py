"""Tests for the Offline Library book-title / book-id cleaner.

Covers clean_title (alias table, fuzzy tiers, review surfacing) and
plan_id_merges (rarity + similarity gates, P-family boundary, leading
zeros, junk digits), plus an end-to-end build_plan/apply_plan round-trip
against a real temp SQLite file. Run from the project root with:
    & .\\study_sync\\Scripts\\python.exe -m unittest discover -s backend/tests -v
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

OFFLINE_DIR = Path(__file__).resolve().parents[1] / "data_loader" / "offline_library"
DATA_LOADER_DIR = OFFLINE_DIR.parent
sys.path.insert(0, str(OFFLINE_DIR))
sys.path.insert(0, str(DATA_LOADER_DIR))

from common import Canonicalizer  # noqa: E402

import book_cleaner as bc  # noqa: E402
import clean_offline_catalog as cli  # noqa: E402


class TitleTests(unittest.TestCase):
    def test_alias_lookup(self):
        cases = {
            "GK": "General Knowledge",
            "G Science": "General Science",
            "G.Sci": "General Science",
            "Hiteck": "Hi-Tech Vijaya Rahasyam",
            "Q.Aptitude": "Quantitative Aptitude",
            "Physics NCERT": "NCERT Physics",
            "Bank IBPS": "IBPS",
            "9th Beelive": "9th Beehive",
        }
        for raw, expected in cases.items():
            self.assertEqual(bc.clean_title(raw), expected, raw)

    def test_ambiguous_short_form_not_aliased(self):
        self.assertEqual(bc.clean_title("G S"), "G S")
        self.assertEqual(bc.clean_title("G.S"), "G.S")

    def test_formatting_normalized_when_no_alias(self):
        self.assertEqual(bc.clean_title('  "Merit  Minds"  '), "Merit Minds")

    def test_fuzzy_tier_via_canonicalizer(self):
        canon = Canonicalizer(lambda *a, **k: None, lambda *a, **k: None, "t")
        first = bc.clean_title("Merit Mind", canon=canon)
        second = bc.clean_title("Merit Minds", canon=canon)
        self.assertEqual(first, "Merit Mind")
        self.assertEqual(second, "Merit Mind")

    def test_review_alias_keys_surface_via_log_review(self):
        notes = []
        result = bc.clean_title("5Gtech", log_review=notes.append)
        self.assertEqual(result, "5G Tech")
        self.assertEqual(len(notes), 1)


class DistanceTests(unittest.TestCase):
    def test_basic_edit_distances(self):
        self.assertEqual(bc.damerau_levenshtein("kitten", "sitting"), 3)
        self.assertEqual(bc.damerau_levenshtein("", "abc"), 3)
        self.assertEqual(bc.damerau_levenshtein("abc", "abc"), 0)

    def test_transposition(self):
        self.assertEqual(bc.damerau_levenshtein("ab", "ba"), 1)
        self.assertEqual(bc.damerau_levenshtein("1680", "1608"), 1)

    def test_leading_insertion_is_one_edit(self):
        self.assertEqual(bc.damerau_levenshtein("680", "1680"), 1)


class MergePlanTests(unittest.TestCase):
    def _plan(self, title_to_ids):
        merges, reviews = bc.plan_id_merges(
            {t: dict(ids) for t, ids in title_to_ids.items()}
        )
        return merges, reviews

    def test_5gtech_variants(self):
        merges, reviews = self._plan({"5G Tech": {"1680": 433, "680": 5, "168": 1, "801": 4}})
        merged = {(m["variant_id"], m["canonical_id"]) for m in merges}
        self.assertEqual(merged, {("680", "1680"), ("168", "1680")})
        self.assertEqual([r["variant_id"] for r in reviews], ["801"])
        self.assertEqual(reviews[0]["reason"], "wrong_id_by_title")
        self.assertEqual(reviews[0]["suggest"], "1680")

    def test_leading_zero_variant_merges(self):
        merges, reviews = self._plan({"History": {"81": 50, "081": 2}})
        self.assertEqual([(m["variant_id"], m["canonical_id"]) for m in merges], [("081", "81")])
        self.assertEqual(reviews, [])

    def test_prefix_junk_merges_within_limit(self):
        merges, reviews = self._plan({"Arithmetic": {"1377": 100, "1377936": 2}})
        self.assertEqual([(m["variant_id"], m["canonical_id"]) for m in merges], [("1377936", "1377")])
        self.assertEqual(reviews, [])

    def test_junk_substring_merges_within_limit(self):
        # pasted/doubled-ID typo: '16791418' contains canonical '1679' as a
        # contiguous substring with 4 stray digits (<= MAX_CONFIRMED_JUNK).
        merges, reviews = self._plan({"Arithmetic": {"1679": 100, "16791418": 2}})
        self.assertEqual([(m["variant_id"], m["canonical_id"]) for m in merges], [("16791418", "1679")])
        self.assertEqual(reviews, [])

    def test_too_much_junk_is_reviewed(self):
        # 7 stray digits exceeds MAX_CONFIRMED_JUNK -> not a confident typo.
        merges, reviews = self._plan({"Arithmetic": {"1679": 100, "16791418412": 2}})
        self.assertEqual(merges, [])
        self.assertEqual(reviews[0]["reason"], "wrong_id_by_title")
        self.assertEqual(reviews[0]["suggest"], "1679")

    def test_rare_two_edit_id_merges(self):
        # edit distance 2, single-use, tiny share of canonical -> auto-merge.
        merges, reviews = self._plan({"5G Tech": {"1680": 100, "1860": 1}})
        self.assertEqual([(m["variant_id"], m["canonical_id"]) for m in merges], [("1860", "1680")])
        self.assertEqual(reviews, [])

    def test_frequent_distinct_ids_stay_separate(self):
        merges, reviews = self._plan({"Arithmetic": {"1543": 30, "1544": 25}})
        self.assertEqual(merges, [])
        self.assertEqual(reviews[0]["reason"], "not_rare")

    def test_rare_but_big_share_is_reviewed(self):
        merges, reviews = self._plan({"Polity": {"100": 3, "200": 2}})
        self.assertEqual(merges, [])
        self.assertEqual(reviews[0]["reason"], "not_rare")

    def test_p_family_boundary_never_crosses(self):
        merges, reviews = self._plan({"Polity": {"P-247": 10, "247": 2}})
        self.assertEqual(merges, [])
        self.assertEqual(reviews[0]["reason"], "not_similar")

    def test_single_id_title_produces_nothing(self):
        merges, reviews = self._plan({"English": {"100": 30}})
        self.assertEqual(merges, [])
        self.assertEqual(reviews, [])

    def test_rarity_boundary_is_inclusive(self):
        merges, reviews = self._plan({"RRB": {"500": 100, "0500": 5}})
        self.assertEqual([(m["variant_id"], m["canonical_id"]) for m in merges], [("0500", "500")])
        self.assertEqual(reviews, [])


class EnhancedDetectionTests(unittest.TestCase):
    def test_metaphone_groups_homophones(self):
        self.assertEqual(bc.metaphone("chemistry"), bc.metaphone("chemestry"))
        self.assertEqual(bc.metaphone("polity"), bc.metaphone("polaty"))
        self.assertEqual(bc.metaphone("science"), bc.metaphone("sience"))

    def test_nfkc_normalization(self):
        self.assertEqual(bc.clean_title("Ｍｅｒｉｔ　Ｍｉｎｄｓ"), "Merit Minds")
        self.assertTrue(bc.ids_near_equivalent("１６８０", "1680"))
        self.assertTrue(bc.ids_near_equivalent("１６８０", "168"))

    def test_word_order_title_merges(self):
        merges, reviews = bc.plan_title_merges(
            {"NCERT Physics": 20, "Physics NCERT": 2}
        )
        self.assertEqual(
            [(m["variant"], m["canonical"]) for m in merges],
            [("Physics NCERT", "NCERT Physics")],
        )
        # word-order swap scores 1.0 on token dice
        s = bc.title_similarity("NCERT Physics", "Physics NCERT")
        self.assertEqual(s["token"], 1.0)
        self.assertGreaterEqual(s["spelling"], bc.TITLE_MERGE_THRESHOLD)

    def test_significant_misspelling_title_merges(self):
        merges, reviews = bc.plan_title_merges(
            {"Quantitative Aptitude": 50, "Quantative Aptitude": 2}
        )
        self.assertEqual(
            [(m["variant"], m["canonical"]) for m in merges],
            [("Quantative Aptitude", "Quantitative Aptitude")],
        )
        self.assertGreaterEqual(merges[0]["score"], bc.TITLE_MERGE_THRESHOLD)

    def test_phonetic_only_match_goes_to_review(self):
        merges, reviews = bc.plan_title_merges({"Science": 20, "Sians": 1})
        self.assertEqual(merges, [])
        self.assertEqual(reviews[0]["variant"], "Sians")
        self.assertEqual(reviews[0]["canonical"], "Science")
        self.assertEqual(reviews[0]["phonetic_score"], 1.0)

    def test_short_acronym_phonetic_collisions_are_rejected(self):
        # metaphone can't tell 'SI' from 'SSC' or 'Disaster' from 'History';
        # short keys / weak phonetic scores must NOT produce proposals.
        for title_totals in (
            {"SSC": 20, "SI": 1},
            {"CTET": 15, "Gate": 1},
            {"History": 100, "Disaster": 5},
        ):
            merges, reviews = bc.plan_title_merges(title_totals)
            self.assertEqual(merges, [], title_totals)
            self.assertEqual(reviews, [], title_totals)

    def test_frequent_distinct_titles_stay_separate(self):
        merges, reviews = bc.plan_title_merges(
            {"General Science": 200, "General Studies": 150}
        )
        self.assertEqual(merges, [])
        self.assertEqual(reviews, [])

    def test_rare_title_review_band(self):
        # spelling similarity 0.78-0.90 -> review with proposed spelling
        merges, reviews = bc.plan_title_merges({"Polity": 100, "Polaty": 2})
        self.assertEqual(merges, [])
        self.assertEqual(reviews[0]["variant"], "Polaty")
        self.assertEqual(reviews[0]["canonical"], "Polity")


class CatalogRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "library.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE students (student_id INTEGER PRIMARY KEY);
            CREATE TABLE books (
                book_id TEXT PRIMARY KEY CHECK(length(trim(book_id)) > 0),
                title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                category TEXT, author TEXT, added_date DATE
            );
            CREATE TABLE offline_library_usage (
                usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date DATE NOT NULL,
                book_id TEXT,
                FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT,
                FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE RESTRICT
            );
            """
        )
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("INSERT INTO students (student_id) VALUES (1)")

        books = [
            ("1680", "5Gtech"),
            ("680", "5Gtech"),
            ("168", "5Gtech"),
            ("801", "5Gtech"),
            ("1377", "Arithmetic"),
            ("1377936", "Arithmetic"),
            ("81", "History"),
            ("081", "History"),
            ("P-247", "Polity"),
            ("247", "Polity"),
            ("900", "Hiteck"),
        ]
        conn.executemany(
            "INSERT INTO books (book_id, title) VALUES (?, ?)", books
        )

        usage = {
            "1680": 100,
            "680": 5,
            "168": 1,
            "801": 4,
            "1377": 100,
            "1377936": 2,
            "81": 50,
            "081": 2,
            "P-247": 10,
            "247": 2,
            "900": 3,
        }
        for book_id, count in usage.items():
            conn.executemany(
                "INSERT INTO offline_library_usage (student_id, date, book_id) "
                "VALUES (1, '2026-08-01', ?)",
                [(book_id,)] * count,
            )
        conn.commit()
        conn.close()

    def tearDown(self):
        self._tmp.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def test_build_plan_finds_expected_merges_and_reviews(self):
        conn = self._connect()
        merges, reviews, renames, title_to_ids, alias_reviews, *_ = cli.build_plan(conn)
        conn.close()
        merged = {(m["variant_id"], m["canonical_id"]) for m in merges}
        self.assertEqual(
            merged, {("680", "1680"), ("168", "1680"), ("1377936", "1377"), ("081", "81")}
        )
        reviewed = {(r["variant_id"], r["canonical_id"]) for r in reviews}
        self.assertEqual(
            reviewed, {("801", "1680"), ("247", "P-247")}
        )
        self.assertEqual(title_to_ids["5G Tech"], {"1680": 100, "680": 5, "168": 1, "801": 4})
        self.assertTrue(any(bid == "900" and old == "Hiteck" and new == "Hi-Tech Vijaya Rahasyam" for bid, old, new in renames))
        self.assertEqual(
            alias_reviews,
            ["catalog: '5Gtech' -> '5G Tech' applied from the curated alias "
             "table (target spelling set by research -- confirm)"],
        )

    def test_dry_run_leaves_database_untouched(self):
        before_conn = self._connect()
        before = before_conn.execute(
            "SELECT book_id FROM books ORDER BY book_id"
        ).fetchall()
        before_conn.close()
        conn = self._connect()
        cli.build_plan(conn)
        conn.close()
        after_conn = self._connect()
        after = after_conn.execute(
            "SELECT book_id FROM books ORDER BY book_id"
        ).fetchall()
        after_conn.close()
        self.assertEqual(before, after)

    def test_apply_rewrites_usage_deletes_orphans_and_renames(self):
        conn = self._connect()
        merges, reviews, renames, *_ = cli.build_plan(conn)
        self.assertNotEqual(merges, [])
        applied, renamed = cli.apply_plan(conn, merges, renames)
        conn.close()
        self.assertEqual(applied, 4)
        self.assertGreaterEqual(renamed, 2)

        conn = self._connect()
        counts = dict(
            conn.execute(
                "SELECT book_id, COUNT(*) FROM offline_library_usage GROUP BY book_id"
            ).fetchall()
        )
        self.assertEqual(counts["1680"], 106)  # 100 + 5 + 1
        self.assertEqual(counts["801"], 4)     # reviewed -> left alone
        self.assertEqual(counts["1377"], 102)
        self.assertEqual(counts["81"], 52)
        for gone in ("680", "168", "1377936", "081"):
            self.assertNotIn(gone, counts)
        self.assertEqual(
            set(b for b, in conn.execute("SELECT book_id FROM books")),
            {"1680", "801", "1377", "81", "P-247", "247", "900"},
        )
        self.assertEqual(
            conn.execute("SELECT title FROM books WHERE book_id = '1680'").fetchone()[0],
            "5G Tech",
        )
        self.assertEqual(
            conn.execute("SELECT title FROM books WHERE book_id = '900'").fetchone()[0],
            "Hi-Tech Vijaya Rahasyam",
        )
        conn.close()

    def test_backup_produces_identical_snapshot(self):
        conn = self._connect()
        backup = cli.backup_db(conn, Path(self._tmp.name) / "backups")
        conn.close()
        self.assertTrue(backup.exists())
        bconn = sqlite3.connect(backup)
        try:
            self.assertEqual(
                bconn.execute("SELECT COUNT(*) FROM books").fetchone()[0], 11
            )
            self.assertEqual(
                bconn.execute("SELECT COUNT(*) FROM offline_library_usage").fetchone()[0],
                279,
            )
        finally:
            bconn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
