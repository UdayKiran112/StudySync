"""Tests for the student CSV cleaning helpers in clean_student_data.py,
focused on the comma-juggled Book ID repair and the Book Name -> catalog id
fill used by the Offline Library build. Run from the project root with:
    & .\\study_sync\\Scripts\\python.exe -m unittest discover -s backend/tests -v
"""

import sys
import unittest
from pathlib import Path

DATA_LOADER_DIR = Path(__file__).resolve().parents[1] / "data_loader"
sys.path.insert(0, str(DATA_LOADER_DIR))

import clean_student_data as csd  # noqa: E402


class FragmentedBookIdRepairTests(unittest.TestCase):
    def test_repeated_id_recommaed(self):
        # "16,801,680" written for "1680,1680" (5GTech has id 1680).
        self.assertEqual(
            csd._repair_fragmented_book_id_cell(
                "16,801,680",
                ["5Gtech", "5Gtech"],
                {"16", "801", "680", "1680"},
                {"1680": "5G Tech"},
            ),
            "1680, 1680",
        )

    def test_repeated_id_recommaed_1661(self):
        # "16,611,661" written for "1661,1661".
        self.assertEqual(
            csd._repair_fragmented_book_id_cell(
                "16,611,661",
                ["DSC", "DSC"],
                {"16", "611", "661", "1661"},
                {"1661": "DSC"},
            ),
            "1661, 1661",
        )

    def test_two_distinct_ids_recommaed(self):
        # "16,301,625" written for "1630,1625".
        self.assertEqual(
            csd._repair_fragmented_book_id_cell(
                "16,301,625",
                ["DSC", "DSCMaths"],
                {"16", "301", "625", "1630", "1625"},
                {"1630": "DSC", "1625": "Maths"},
            ),
            "1630, 1625",
        )

    def test_repeated_id_recommaed_1556(self):
        # "15,561,556" written for "1556,1556".
        self.assertEqual(
            csd._repair_fragmented_book_id_cell(
                "15,561,556",
                ["Maths", "Maths"],
                {"15", "561", "556", "1556"},
                {"1556": "Maths"},
            ),
            "1556, 1556",
        )

    def test_correct_multi_id_cell_left_alone(self):
        # "1699, 1661" is a real two-book cell: the digit run re-segments to
        # itself, so it must not be "repaired".
        self.assertIsNone(
            csd._repair_fragmented_book_id_cell(
                "1699, 1661",
                ["DSC", "DSC"],
                {"1699", "1661", "169", "91", "166", "61"},
                {"1699": "DSC", "1661": "DSC"},
            )
        )

    def test_correct_repeated_id_cell_left_alone(self):
        self.assertIsNone(
            csd._repair_fragmented_book_id_cell(
                "1680, 1680",
                ["5G Tech", "5G Tech"],
                {"1680"},
                {"1680": "5G Tech"},
            )
        )

    def test_genuine_three_book_cell_left_alone(self):
        # A real three-id cell has three recorded names, so the two-id
        # reconstruction can never match and must not fire.
        self.assertIsNone(
            csd._repair_fragmented_book_id_cell(
                "16, 801, 680",
                ["5Gtech", "5Gtech", "DSC"],
                {"16", "801", "680", "1680"},
                {"1680": "5G Tech"},
            )
        )

    def test_no_book_names_no_repair(self):
        self.assertIsNone(
            csd._repair_fragmented_book_id_cell(
                "16,801,680", [], {"16", "801", "680", "1680"}, {"1680": "5G Tech"}
            )
        )

    def test_non_numeric_fragment_no_repair(self):
        self.assertIsNone(
            csd._repair_fragmented_book_id_cell(
                "16,801,P-3", ["5Gtech", "5Gtech"], {"16", "801", "680", "1680"}, {}
            )
        )

    def test_single_fragment_no_repair(self):
        self.assertIsNone(
            csd._repair_fragmented_book_id_cell(
                "1680", ["5G Tech"], {"1680"}, {"1680": "5G Tech"}
            )
        )

    def test_title_mismatch_no_repair(self):
        # Re-segments to valid ids but the titles disagree with the names.
        self.assertIsNone(
            csd._repair_fragmented_book_id_cell(
                "16,801,680",
                ["Maths", "Maths"],
                {"16", "801", "680", "1680"},
                {"1680": "5G Tech"},
            )
        )

    def test_ambiguous_segmentation_no_repair(self):
        # "14,161,416" re-segments three ways of equal length/score
        # (1416|1416, 141|616|16, 14|1614|16, 1416|14|16), so nothing is
        # guessed.
        self.assertIsNone(
            csd._repair_fragmented_book_id_cell(
                "14,161,416",
                ["DSC", "DSC", "DSC"],
                {"14", "161", "416", "1416", "141", "616", "1614", "16"},
                {
                    "1416": "DSC",
                    "141": "DSC",
                    "616": "DSC",
                    "1614": "DSC",
                    "14": "DSC",
                    "16": "DSC",
                },
            )
        )


class CatalogReferenceTests(unittest.TestCase):
    def _df(self):
        import pandas as pd

        return pd.DataFrame(
            {
                "Book ID": ["1680", "1680", "16,801,680", "1680", "16,301,625"],
                "Reference Book": [
                    "5G Tech",
                    "5G Tech",
                    "5Gtech,5Gtech",
                    "5Gtech",
                    "DSC,DSCMaths",
                ],
            }
        )

    def test_reference_uses_only_well_formed_rows(self):
        valid_ids, id_to_title = csd._offline_catalog_reference(self._df())
        # Multi-book rows (comma in either cell) must not pollute the mapping.
        self.assertIn("1680", valid_ids)
        self.assertNotIn("16", valid_ids)
        self.assertNotIn("801", valid_ids)
        self.assertNotIn("680", valid_ids)
        # Majority title across the well-formed rows.
        self.assertEqual(id_to_title["1680"], "5G Tech")


class BookNameToIdFillTests(unittest.TestCase):
    def test_exact_unique_match(self):
        self.assertEqual(
            csd._resolve_id_from_book_name("Current Affairs", {"1646": "Current Affairs"}),
            "1646",
        )

    def test_substring_of_title_match(self):
        self.assertEqual(
            csd._resolve_id_from_book_name("Account", {"849": "Accounts"}),
            "849",
        )

    def test_typo_close_match(self):
        self.assertEqual(
            csd._resolve_id_from_book_name("Curret Affairs", {"1646": "Current Affairs"}),
            "1646",
        )

    def test_letter_overlap_alone_not_enough(self):
        # 'Shine' shares 83% of its letters with 'Engish' but is a magazine,
        # not the English book - must stay unfilled.
        self.assertIsNone(
            csd._resolve_id_from_book_name("Shine", {"1117": "Engish"})
        )

    def test_disjoint_name_left_alone(self):
        self.assertIsNone(
            csd._resolve_id_from_book_name("Indian History", {"841": "Disctionary"})
        )

    def test_ambiguous_title_left_alone(self):
        self.assertIsNone(
            csd._resolve_id_from_book_name("Maths", {"1254": "Maths", "1556": "Maths"})
        )

    def test_empty_name_no_match(self):
        self.assertIsNone(csd._resolve_id_from_book_name("  ", {"1": "Maths"}))


if __name__ == "__main__":
    unittest.main()
