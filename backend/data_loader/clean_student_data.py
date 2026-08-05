"""
clean_student_data.py
======================

Cleans the messy, inconsistent "Student details" export and splits it into
four separate, well-formed section files, each written directly into its
own project folder (sibling to that section's load_*.py script):

    1. digital_library/digital_library.csv
    2. offline_library/offline_library.csv
    3. coaching/digital_class.csv
    4. attendance/attendance.csv
    5. marks/offline_exam.csv

Every record that is missing, invalid, or irregular in a way that could not
be safely auto-corrected is written to that section's own
error_log_<section>.log (with a corrections_log_<section>.log alongside it
for changes that WERE auto-corrected), each with a plain-English
description of the problem and a reference back to the original row in the
source spreadsheet so it can be fixed by hand. The one exception is
error_log_general.log / corrections_log_general.log (Student ID / Name /
Date issues, which affect every section) -- those aren't specific to one
folder, so they're written at the reports root instead.

Logs and reports live in the shared pipeline reports tree (reports/, one
subfolder per module -- see common.py / run_pipeline.py), never next to the
CSV files. The CSV files themselves stay in the section folders above,
because that's where the loaders read them from.

USAGE
-----
    python clean_student_data.py input.csv

Writes into the project folders next to this script by default. Pass a
second argument to redirect everything under a separate folder instead
(each section still gets its own subfolder under it) -- useful for a test
run without touching the real project folders:

    python clean_student_data.py input.csv test_output/
"""

import csv
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from common import (
    CLOSE_TIME,
    OPEN_TIME,
    Canonicalizer,
    clamp_out_time,
    fix_checkin_pm_offset,
    fix_checkout_pm_offset,
)

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

# How many non-data rows sit above the real header row in the raw export
# (in this sheet: 2 blank rows, then the header row).
HEADER_SKIPROWS = 2

# Known misspellings / casing variants -> canonical value
GENDER_MAP = {
    "male": "Male",
    "male ": "Male",
    "female": "Female",
    "famale": "Female",
    "femlae": "Female",
}

# A value counts as "Subscription" if, once whitespace/punctuation is
# stripped, it *starts with* something that looks like the word
# "subscription" (covers Subcription, Subscrption, Subsciption,
# Subscritption, Subscription,Subscription, etc.)
SUBSCRIPTION_LIKE = re.compile(r"^sub[sc]", re.IGNORECASE)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_time(raw: str):
    """Recover an HH:MM time from common data-entry typos: '.', ';', or '"'
    used instead of ':' (e.g. '10.06', '12;33', '16"24'), a doubled colon
    ('16::33'), or the colon dropped entirely ('1010', '1700'). Returns the
    corrected string, or None if the value can't be safely recovered."""
    candidate = re.sub(r'[.;"]', ":", raw)
    candidate = re.sub(r":{2,}", ":", candidate)
    if TIME_RE.match(candidate):
        return candidate
    if candidate.isdigit() and len(candidate) in (3, 4):
        candidate2 = (
            (candidate[0] + ":" + candidate[1:])
            if len(candidate) == 3
            else (candidate[:2] + ":" + candidate[2:])
        )
        if TIME_RE.match(candidate2):
            return candidate2
    return None


LUNCH_START = "13:00"
LUNCH_END = "14:00"


def classify_session(in_time: str, out_time: str):
    """Mirrors the attendance session rule (see load_attendance.py):
      - out_time <= 13:00                          -> 'morning'   (case 1)
      - in_time  >= 13:00                          -> 'afternoon' (case 2)
      - in_time < 13:00 and out_time > 13:00 (spans)-> 'full_day'  (case 3)
      - out_time missing/unparseable                -> 'open' (incomplete
        session; treated like a single case 1/2 session, never narrowed)
    Returns None if in_time itself isn't a valid time."""
    if not TIME_RE.match(in_time):
        return None
    if not TIME_RE.match(out_time):
        return "open"
    if out_time <= LUNCH_START:
        return "morning"
    if in_time >= LUNCH_START:
        return "afternoon"
    return "full_day"


# Reasonable date bounds for this dataset - anything outside this window is
# treated as suspicious rather than blindly trusted.
DATE_FLOOR = datetime(2020, 1, 1)
DATE_CEIL = datetime.today() + timedelta(days=1)

# A freshly-parsed date that jumps more than this many days away from the
# previous good date is assumed to be a typo/autofill error rather than a
# genuine gap in attendance.
MAX_PLAUSIBLE_JUMP_DAYS = 60


# --------------------------------------------------------------------------
# ERROR LOG
# --------------------------------------------------------------------------


class ErrorLog:
    """Collects one entry per problem found, tagged with which section it
    affects and whether it was auto-corrected or needs manual review, so
    two sets of .log files can be written per section:

      error_log_<section>.log       - things that still need manual review
      corrections_log_<section>.log - things the script fixed automatically
    """

    # Display order / friendly titles for each section's log file.
    SECTIONS = [
        ("general", "General (Student ID / Name / Date - affects every section)"),
        ("digital_library", "Digital Library"),
        ("offline_library", "Offline Library"),
        ("digital_class", "Digital Class"),
        ("attendance", "Attendance"),
        ("offline_exam", "Offline Exam"),
    ]

    # Which project folder each section's CSV/logs land in, matching the
    # sibling folders that hold each domain's load_*.py script (see the
    # tree at the project root). "digital_class" maps to "coaching" since
    # that's the only remaining folder without an obvious section of its
    # own -- rename this mapping if load_coaching.py actually expects a
    # different file/folder. "offline_exam" lands in "marks" (alongside
    # load_exam_marks.py). "general" has no folder of its own (its issues
    # aren't specific to one section) and is written at the project root
    # instead -- see write_all.
    SECTION_FOLDERS = {
        "digital_library": "digital_library",
        "offline_library": "offline_library",
        "digital_class": "coaching",
        "attendance": "attendance",
        "offline_exam": "marks",
    }

    def __init__(self):
        self.rows = []

    def add(
        self,
        section,
        status,
        excel_row,
        sl_no,
        student_id,
        student_name,
        issue,
        raw_value="",
    ):
        assert section in dict(self.SECTIONS), f"Unknown log section: {section}"
        assert status in ("review", "corrected"), f"Unknown log status: {status}"
        self.rows.append(
            {
                "section": section,
                "status": status,
                "excel_row": excel_row,
                "sl_no": sl_no,
                "student_id": student_id,
                "student_name": student_name,
                "issue": issue,
                "raw_value": raw_value,
            }
        )

    @staticmethod
    def _group_key(issue: str) -> str:
        # Collapse variable parts (like specific old/new values) so similar
        # problems are grouped together under one heading.
        return re.sub(r"'[^']*'", "'...'", issue)

    def _render(
        self,
        heading: str,
        title: str,
        entries: list,
        source_file: str,
        empty_message: str,
    ) -> str:
        groups = {}
        for entry in entries:
            groups.setdefault(self._group_key(entry["issue"]), []).append(entry)

        lines = []
        lines.append("=" * 78)
        lines.append(f"STUDENT DATA CLEANUP - {heading}: {title.upper()}")
        lines.append("=" * 78)
        lines.append(f"Source file   : {source_file}")
        lines.append(f"Generated     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Total entries : {len(entries)}")
        lines.append("")

        if not entries:
            lines.append(empty_message)
            return "\n".join(lines) + "\n"

        lines.append("Summary by type:")
        for key in sorted(groups, key=lambda k: -len(groups[k])):
            lines.append(f"  - {len(groups[key]):>5}  {key}")
        lines.append("")
        lines.append("=" * 78)
        lines.append("DETAILS (grouped by type)")
        lines.append("=" * 78)

        for key in sorted(groups, key=lambda k: -len(groups[k])):
            group_entries = sorted(groups[key], key=lambda e: e["excel_row"])
            lines.append("")
            lines.append(f"--- {key}  ({len(group_entries)} occurrence(s)) ---")
            for e in group_entries:
                student = e["student_name"] or "(missing)"
                sid = e["student_id"] or "(missing)"
                loc = f"Excel row {e['excel_row']} (Serial No. {e['sl_no']})"
                detail = f"    [{loc}] Student: {student} (ID: {sid}) - {e['issue']}"
                if e["raw_value"]:
                    detail += f"  [original value: {e['raw_value']!r}]"
                lines.append(detail)

        return "\n".join(lines) + "\n"

    def write_all(self, base_dir: Path, source_file: str):
        """Writes two .log files per section:
        error_log_<section>.log       - needs manual review
        corrections_log_<section>.log - auto-corrected, FYI only

        Each section's pair lands in the shared reports tree (see
        common.module_report_dir) under the subfolder for that section's
        module (reports/<module>/), alongside that module's loader report.
        "general" has no module folder of its own -- its pair is written
        directly into the reports root instead.
        """

        reports_dir = base_dir / "reports"
        for section_key, title in self.SECTIONS:
            folder = self.SECTION_FOLDERS.get(section_key)
            target_dir = (reports_dir / folder) if folder else reports_dir
            target_dir.mkdir(parents=True, exist_ok=True)

            review_entries = [
                e
                for e in self.rows
                if e["section"] == section_key and e["status"] == "review"
            ]
            corrected_entries = [
                e
                for e in self.rows
                if e["section"] == section_key and e["status"] == "corrected"
            ]

            review_text = self._render(
                "ERROR LOG",
                title,
                review_entries,
                source_file,
                "No issues found for this section.",
            )
            (target_dir / f"error_log_{section_key}.log").write_text(
                review_text, encoding="utf-8"
            )

            corrections_text = self._render(
                "CORRECTIONS LOG",
                title,
                corrected_entries,
                source_file,
                "No auto-corrections were made for this section.",
            )
            (target_dir / f"corrections_log_{section_key}.log").write_text(
                corrections_text, encoding="utf-8"
            )


# --------------------------------------------------------------------------
# LOADING
# --------------------------------------------------------------------------


def load_raw(path: Path) -> pd.DataFrame:
    """Load the export, drop decorative blank rows/columns, keep an
    'Excel Row' reference column that points back to the original file."""

    df = pd.read_csv(path, skiprows=HEADER_SKIPROWS, dtype=str, low_memory=False)

    # Drop the trailing unlabeled columns Excel/Sheets sometimes exports
    df = df.loc[:, ~df.columns.str.match(r"^Unnamed")]

    # Row N of this dataframe is line (N + HEADER_SKIPROWS + 2) of the raw file
    # (+1 for the header row itself, +1 to go from 0-based to 1-based).
    df["Excel Row"] = df.index + HEADER_SKIPROWS + 2

    # Strip whitespace on every text cell
    for col in df.columns:
        if col != "Excel Row":
            df[col] = df[col].fillna("").astype(str).str.strip()

    # Drop rows that are entirely blank (trailing padding rows some sheets
    # keep after the last real record)
    data_cols = [c for c in df.columns if c != "Excel Row"]
    is_blank = (df[data_cols] == "").all(axis=1)
    df = df.loc[~is_blank].reset_index(drop=True)

    return df


# --------------------------------------------------------------------------
# FIELD-LEVEL CLEANING
# --------------------------------------------------------------------------


def clean_gender(value: str) -> str:
    if value == "":
        return ""
    return GENDER_MAP.get(value.lower(), value if value in ("Male", "Female") else "")


def is_subscription(value: str) -> bool:
    return bool(SUBSCRIPTION_LIKE.match(value.replace(" ", "").replace(",", "")))


def collapse(value: str) -> str:
    """Trim whitespace around a single item taken from a comma-split cell."""
    return value.strip()


def parse_date(raw: str):
    """Try to turn a raw date string into a datetime, tolerating the ':' vs
    '.' separator typo seen in the sheet. Returns None if unparsable."""
    if raw == "":
        return None
    candidate = raw.replace(":", ".").replace("/", ".").replace("-", ".")
    parts = [p for p in candidate.split(".") if p != ""]
    if len(parts) != 3:
        return None
    day, month, year = parts
    try:
        return datetime(int(year), int(month), int(day))
    except ValueError:
        return None


def fix_dates(df: pd.DataFrame, log: ErrorLog) -> pd.DataFrame:
    """Adds a 'Date_clean' column (datetime, or None if unrecoverable),
    correcting obvious autofill/typo errors (e.g. day.month staying the
    same while the year increments nonsensically) by carrying forward the
    last plausible date."""

    last_good = None
    cleaned = []

    for idx, row in df.iterrows():
        raw = row["Date"]
        excel_row = row["Excel Row"]
        sl_no = row["Sl.No"]
        student_id = row["ID NO"]
        student_name = row["Name of the Student"]

        parsed = parse_date(raw)

        if parsed is None:
            log.add(
                "general",
                "review",
                excel_row,
                sl_no,
                student_id,
                student_name,
                "Date could not be parsed",
                raw,
            )
            cleaned.append(None)
            continue

        plausible = DATE_FLOOR <= parsed <= DATE_CEIL and (
            last_good is None
            or abs((parsed - last_good).days) <= MAX_PLAUSIBLE_JUMP_DAYS
        )

        if plausible:
            cleaned.append(parsed)
            last_good = parsed
        elif last_good is not None:
            # Try re-using day/month with the last known-good year, which
            # fixes the classic "autofill dragged the year forward" bug.
            try:
                corrected = parsed.replace(year=last_good.year)
            except ValueError:
                corrected = None
            if (
                corrected
                and abs((corrected - last_good).days) <= MAX_PLAUSIBLE_JUMP_DAYS
            ):
                log.add(
                    "general",
                    "corrected",
                    excel_row,
                    sl_no,
                    student_id,
                    student_name,
                    f"Date auto-corrected from '{raw}' to '{corrected.strftime('%d.%m.%Y')}' "
                    f"(looked like a copy/autofill error - please verify)",
                    raw,
                )
                cleaned.append(corrected)
                last_good = corrected
            else:
                log.add(
                    "general",
                    "review",
                    excel_row,
                    sl_no,
                    student_id,
                    student_name,
                    "Date is out of the expected range / sequence and was left as-is",
                    raw,
                )
                cleaned.append(parsed)
        else:
            cleaned.append(parsed)
            last_good = parsed

    df = df.copy()
    df["Date_clean"] = cleaned
    return df


def zero_pad_time(value: str) -> str:
    """'9:38' -> '09:38'. Assumes value already matches TIME_RE."""
    h, m = value.split(":")
    return f"{int(h):02d}:{m}"


def fix_times(df: pd.DataFrame, log: ErrorLog) -> pd.DataFrame:
    """Normalizes recoverable IN/OUT time typos in place (see
    normalize_time) and zero-pads single-digit hours (e.g. '9:38' ->
    '09:38') so later same-length string comparisons against '13:00' /
    '14:00' (used to detect lunch-spanning sessions) work correctly. This
    runs once, before Attendance/Digital Library are built separately, so
    both sections benefit and each correction is only logged once (under
    'general', since IN/OUT are shared source columns)."""

    df = df.copy()
    for col, label in (("IN", "In Time"), ("OUT", "Out Time")):
        fixed_values = []
        for idx, row in df.iterrows():
            raw = row[col]
            if raw == "":
                fixed_values.append(raw)
                continue
            if TIME_RE.match(raw):
                canon = zero_pad_time(raw)
                if canon != raw:
                    log.add(
                        "general",
                        "corrected",
                        row["Excel Row"],
                        row["Sl.No"],
                        row["ID NO"],
                        row["Name of the Student"],
                        f"{label} value zero-padded from '{raw}' to '{canon}'",
                        raw,
                    )
                fixed_values.append(canon)
                continue
            fixed = normalize_time(raw)
            if fixed is not None:
                canon = zero_pad_time(fixed)
                log.add(
                    "general",
                    "corrected",
                    row["Excel Row"],
                    row["Sl.No"],
                    row["ID NO"],
                    row["Name of the Student"],
                    f"{label} value normalized from '{raw}' to '{canon}' (separator/typo corrected)",
                    raw,
                )
                fixed_values.append(canon)
            else:
                fixed_values.append(
                    raw
                )  # left as-is; section builders flag it as still-invalid
        df[col] = fixed_values
    return df


def fix_operating_hours(df: pd.DataFrame, log: ErrorLog) -> pd.DataFrame:
    """Corrects 12-hour-clock slips and out-of-operating-hours times on the
    shared IN/OUT columns, after fix_times has normalized the formatting:

      - A check-in before 09:00 (the library's opening time) is almost always
        an afternoon time recorded on a 12-hour clock without the PM offset
        ('02:00' really meant 14:00) -> +12h. This dataset contains a whole
        block of them (40+ rows on one day). After the fix, any row whose
        pair is still inconsistent (fixed-in not before its out) fails the
        downstream check_out > check_in constraint and is skipped with a log
        rather than loaded with nonsense times.
      - A check-out before its check-in that a 12-hour clock explains
        ('01:00' meant 13:00) -> +12h.
      - A check-out past 18:00 (the library's latest closing) is a
        data-entry overrun -> clamped to 18:00.

    Runs once, before the per-section builders, so attendance and digital
    library both see the corrected times and each correction is logged only
    once (under 'general', since IN/OUT are shared source columns)."""
    df = df.copy()
    for idx, row in df.iterrows():
        excel_row = row["Excel Row"]
        sl_no = row["Sl.No"]
        sid = row["ID NO"]
        sname = row["Name of the Student"]
        raw_in, raw_out = row["IN"], row["OUT"]
        new_in, new_out = raw_in, raw_out

        if TIME_RE.match(new_in) and new_in < OPEN_TIME:
            fixed_in = fix_checkin_pm_offset(new_in, new_out)
            if fixed_in is not None:
                log.add(
                    "general",
                    "corrected",
                    excel_row,
                    sl_no,
                    sid,
                    sname,
                    f"In Time before the {OPEN_TIME} opening read as a "
                    f"12-hour-clock PM time - corrected from '{new_in}' to "
                    f"'{fixed_in}' (if the fixed pair is still inconsistent "
                    f"it is skipped downstream, not guessed)",
                    raw_in,
                )
                new_in = fixed_in
            else:
                log.add(
                    "general",
                    "review",
                    excel_row,
                    sl_no,
                    sid,
                    sname,
                    f"In Time is before the {OPEN_TIME} opening and no "
                    f"12-hour-clock interpretation fits - please verify",
                    raw_in,
                )

        if TIME_RE.match(new_in) and TIME_RE.match(new_out):
            fixed_out = fix_checkout_pm_offset(new_in, new_out)
            if fixed_out is not None:
                log.add(
                    "general",
                    "corrected",
                    excel_row,
                    sl_no,
                    sid,
                    sname,
                    f"Out Time before In Time read as a 12-hour-clock PM "
                    f"time - corrected from '{new_out}' to '{fixed_out}'",
                    raw_out,
                )
                new_out = fixed_out

        if TIME_RE.match(new_out):
            clamped = clamp_out_time(new_out)
            if clamped is not None:
                log.add(
                    "general",
                    "corrected",
                    excel_row,
                    sl_no,
                    sid,
                    sname,
                    f"Out Time past the {CLOSE_TIME} closing time clamped to "
                    f"'{clamped}'",
                    raw_out,
                )
                new_out = clamped

        if (new_in, new_out) != (raw_in, raw_out):
            df.at[idx, "IN"] = new_in
            df.at[idx, "OUT"] = new_out
    return df


# --------------------------------------------------------------------------
# SECTION BUILDERS
# --------------------------------------------------------------------------


def validate_core_fields(df: pd.DataFrame, log: ErrorLog) -> pd.DataFrame:
    """Flags/removes rows missing the fields every section depends on:
    Student ID and Student Name. Returns only the rows that are usable."""

    ok = pd.Series(True, index=df.index)

    missing_id = df["ID NO"] == ""
    missing_name = df["Name of the Student"] == ""

    for idx in df.index[missing_id]:
        r = df.loc[idx]
        log.add(
            "general",
            "review",
            r["Excel Row"],
            r["Sl.No"],
            r["ID NO"],
            r["Name of the Student"],
            "Missing Student ID - record dropped from all sections",
        )
    for idx in df.index[missing_name & ~missing_id]:
        r = df.loc[idx]
        log.add(
            "general",
            "review",
            r["Excel Row"],
            r["Sl.No"],
            r["ID NO"],
            r["Name of the Student"],
            "Missing Student Name - record dropped from all sections",
        )

    ok &= ~missing_id & ~missing_name
    return df.loc[ok].copy()


def build_digital_library(df: pd.DataFrame, log: ErrorLog) -> pd.DataFrame:
    """One row per platform actually used. A cell can list several
    comma-separated platform/purpose values (e.g. 'IACE, Youtube') that got
    merged at data-entry time instead of being recorded as separate visits
    -- each becomes its own row here. Account Name (platform_name) is
    mandatory downstream (digital_library_usage.platform_name is NOT NULL),
    so unlike offline library's Book ID/Name split, a shorter list is always
    padded by reusing its last value rather than leaving a blank."""

    mask = (
        (df["Digital Library"] != "")
        | (df["Purpose"] != "")
        | (df["Online Subscription"] != "")
    )
    sub = df.loc[mask].copy()

    # Case 3 (full-day attendance spanning the lunch break) is narrowed to
    # a single half rather than the whole day; this toggle alternates which
    # half gets used across the dataset so digital usage isn't skewed
    # entirely morning or entirely afternoon.
    full_day_toggle = [0]

    records = []
    for idx, row in sub.iterrows():
        val = row["Online Subscription"]
        if val != "" and not is_subscription(val):
            log.add(
                "digital_library",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Unrecognized value in 'Online Subscription' column "
                "(expected blank or some form of 'Subscription') - treated as 'Own', please verify",
                val,
            )
        elif (
            val != ""
            and val.strip().lower().replace(" ", "").replace(",", "") != "subscription"
        ):
            log.add(
                "digital_library",
                "corrected",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Online Subscription value normalized to 'Subscription' (misspelling corrected) "
                "-> Account Type set to 'Library Subscription'",
                val,
            )
        account_type = "Library Subscription" if is_subscription(val) else "Own"

        date_str = (
            row["Date_clean"].strftime("%d-%m-%Y")
            if pd.notna(row["Date_clean"])
            else row["Date"]
        )

        # Validate In/Out time here too (separately from the Attendance
        # section's own check): digital_library_usage.in_time is NOT NULL,
        # so a row missing it can't be loaded as digital library usage even
        # if the same IN/OUT columns are fine for Attendance.
        in_time, out_time = row["IN"], row["OUT"]
        if in_time == "":
            log.add(
                "digital_library",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Missing In Time - required for digital library usage, this row cannot be loaded until fixed",
            )
        elif not TIME_RE.match(in_time):
            log.add(
                "digital_library",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "In Time is not in HH:MM format",
                in_time,
            )
        if out_time != "" and not TIME_RE.match(out_time):
            log.add(
                "digital_library",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Out Time is not in HH:MM format",
                out_time,
            )
        if TIME_RE.match(in_time) and TIME_RE.match(out_time):
            if datetime.strptime(out_time, "%H:%M") <= datetime.strptime(
                in_time, "%H:%M"
            ):
                log.add(
                    "digital_library",
                    "review",
                    row["Excel Row"],
                    row["Sl.No"],
                    row["ID NO"],
                    row["Name of the Student"],
                    "Out Time is not after In Time",
                    f"IN={in_time} OUT={out_time}",
                )

        # Attendance covers the student's whole visit, but a digital
        # library session is only part of that. Cases 1/2 (a single
        # morning-only or afternoon-only visit) already work as one
        # session, so the attendance In/Out Time is used as-is. Case 3
        # (in before 13:00, out after 13:00 -- a full day spanning lunch)
        # is narrowed to just one half, alternating morning/afternoon
        # across such rows, rather than implying a digital session that
        # spanned the entire day.
        digital_in, digital_out = in_time, out_time
        if classify_session(in_time, out_time) == "full_day":
            # Only a genuine choice if Out Time is actually past the lunch
            # window (14:00) -- if it falls DURING lunch (13:00-14:00), a
            # '14:00 -> out_time' segment would be invalid (out before in),
            # so Morning is the only usable half in that edge case.
            use_afternoon = (full_day_toggle[0] % 2 == 1) and (out_time > LUNCH_END)
            if use_afternoon:
                digital_in, digital_out = LUNCH_END, out_time
                segment_desc = f"Afternoon ({LUNCH_END}-{out_time})"
            else:
                digital_in, digital_out = in_time, LUNCH_START
                segment_desc = f"Morning ({in_time}-{LUNCH_START})"
            full_day_toggle[0] += 1
            log.add(
                "digital_library",
                "corrected",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                f"Full-day attendance ({in_time}-{out_time}) spans the lunch break - digital "
                f"library timing narrowed to a single {segment_desc} session (alternating "
                f"morning/afternoon across such rows) instead of implying use for the whole day",
                f"IN={in_time} OUT={out_time}",
            )

        platforms = [
            collapse(x) for x in row["Digital Library"].split(",") if collapse(x) != ""
        ]
        purposes = [collapse(x) for x in row["Purpose"].split(",") if collapse(x) != ""]
        n = max(len(platforms), len(purposes))
        if n == 0:
            # An Online Subscription value with no platform name at all --
            # can't be loaded (platform_name is NOT NULL downstream either).
            log.add(
                "digital_library",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Online Subscription/Purpose present but no Account Name (platform) recorded",
            )
            continue

        if len(platforms) != len(purposes) and platforms and purposes:
            log.add(
                "digital_library",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                f"Account Name list ({len(platforms)} item(s)) and Purpose list "
                f"({len(purposes)} item(s)) don't match up - split into {n} row(s), "
                f"reusing the last value to fill the gap, please verify",
                f"Digital Library='{row['Digital Library']}' | Purpose='{row['Purpose']}'",
            )
        if n > 1:
            log.add(
                "digital_library",
                "corrected",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                f"Account Name/Purpose cell had {n} comma-separated value(s) - split into "
                f"{n} separate digital library row(s) instead of one combined row",
                f"Digital Library='{row['Digital Library']}' | Purpose='{row['Purpose']}'",
            )

        platforms_padded = (
            platforms + [platforms[-1]] * (n - len(platforms))
            if platforms
            else [""] * n
        )
        purposes_padded = (
            purposes + [purposes[-1]] * (n - len(purposes)) if purposes else [""] * n
        )

        for platform, purpose in zip(platforms_padded, purposes_padded):
            if platform == "":
                log.add(
                    "digital_library",
                    "review",
                    row["Excel Row"],
                    row["Sl.No"],
                    row["ID NO"],
                    row["Name of the Student"],
                    "Purpose recorded with no Account Name (platform) - cannot be loaded",
                )
                continue
            records.append(
                {
                    "Serial No.": row["Sl.No"],
                    "Date": date_str,
                    "Student ID": row["ID NO"],
                    "Student Name": row["Name of the Student"],
                    "Account Name": platform,
                    "Account Type": account_type,
                    "Purpose": purpose,
                    "In Time": digital_in,
                    "Out Time": digital_out,
                }
            )

    return pd.DataFrame(
        records,
        columns=[
            "Serial No.",
            "Date",
            "Student ID",
            "Student Name",
            "Account Name",
            "Account Type",
            "Purpose",
            "In Time",
            "Out Time",
        ],
    )


# Matches a '.' used as a list separator between two numeric book IDs
# (e.g. "1642. 1565"), as opposed to a decimal point or part of a code.
BOOK_ID_DOT_SEPARATOR = re.compile(r"(\d)\s*\.\s*(\d)")

# Matches a trailing '(<digits>)' on a Digital Class value, e.g. the
# "(23)" in "Reasoning Class(23)" -- a headcount that got appended onto
# the class name at data-entry time instead of being recorded separately.
# Anchored to the end so a genuine parenthetical inside the name (e.g.
# "Current Affairs(Feb)(23)") is left alone and only the trailing count is
# stripped.
CLASS_COUNT_SUFFIX = re.compile(r"\s*\(\s*(\d+)\s*\)\s*$")


# A Book ID cell that is actually a time of day ('12:25', '12;42', '15:40')
# -- someone typed the time in the ID column. Not a catalog id.
TIME_LIKE_BOOK_ID = re.compile(r"^\d{1,2}[:;.]\d{1,2}$")


def expand_book_id_cell(raw: str, log: ErrorLog, excel_row, sl_no, sid, sname):
    """Split a Book ID cell into its individual ids, handling the junk this
    column actually contains beyond a plain comma list:
      - time-of-day values ('12:25') are dropped (a review log entry; the
        Book Name, if any, is kept as an un-catalogued item),
      - a space-separated run of digits ('1254 1540') is two ids typed into
        one cell, so it is split into one row per id.
    Returns the list of usable id strings."""
    ids = []
    for token in re.split(r"[,;]", raw):
        token = token.strip()
        if not token:
            continue
        if TIME_LIKE_BOOK_ID.match(token):
            log.add(
                "offline_library",
                "review",
                excel_row,
                sl_no,
                sid,
                sname,
                f"Book ID {token!r} looks like a time, not a catalog id - "
                f"dropped (any Book Name is kept as an un-catalogued item)",
                token,
            )
            continue
        parts = token.split()
        if len(parts) > 1 and all(p.isdigit() for p in parts):
            log.add(
                "offline_library",
                "corrected",
                excel_row,
                sl_no,
                sid,
                sname,
                f"Book ID cell held {len(parts)} space-separated ids - "
                f"split into one row per id",
                token,
            )
            ids.extend(parts)
        else:
            ids.append(token)
    return ids


def build_offline_library(df: pd.DataFrame, log: ErrorLog) -> pd.DataFrame:
    """Multiple comma-separated Book IDs mean the student took out multiple
    books in one visit - each one becomes its own row here, rather than
    being kept as a single combined record."""

    mask = (df["Book ID"] != "") | (df["Reference Book"] != "")
    sub = df.loc[mask].copy()

    records = []
    for idx, row in sub.iterrows():
        raw_book_id = row["Book ID"]
        raw_book_name = row["Reference Book"]
        date_str = (
            row["Date_clean"].strftime("%d-%m-%Y")
            if pd.notna(row["Date_clean"])
            else row["Date"]
        )

        # Fix the occasional typo where '.' was used instead of ',' to
        # separate multiple numeric book IDs (e.g. "1642. 1565").
        normalized_id = BOOK_ID_DOT_SEPARATOR.sub(r"\1, \2", raw_book_id)
        if normalized_id != raw_book_id:
            log.add(
                "offline_library",
                "corrected",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Book ID used '.' instead of ',' to separate multiple books - corrected",
                raw_book_id,
            )

        book_ids = expand_book_id_cell(
            normalized_id,
            log,
            row["Excel Row"],
            row["Sl.No"],
            row["ID NO"],
            row["Name of the Student"],
        )
        book_names = [b.strip() for b in raw_book_name.split(",") if b.strip() != ""]

        def add_record(bid, bname):
            records.append(
                {
                    "Serial No.": row["Sl.No"],
                    "Date": date_str,
                    "Student ID": row["ID NO"],
                    "Student Name": row["Name of the Student"],
                    "Book ID": bid,
                    "Book Name": bname,
                }
            )

        if not book_ids and not book_names:
            continue

        elif book_ids and not book_names:
            # Book(s) taken but no name recorded at all - a real gap worth reviewing.
            log.add(
                "offline_library",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                f"{len(book_ids)} Book ID(s) present but Book Name is missing",
                raw_book_id,
            )
            for bid in book_ids:
                add_record(bid, "")

        elif book_names and not book_ids:
            # Un-catalogued items (e.g. "Self", "Shine India" magazines) are a
            # normal pattern with no Book ID - just one row per named item.
            for bname in book_names:
                add_record("", bname)

        else:
            # Both present. Multiple IDs -> multiple books taken.
            target_len = max(len(book_ids), len(book_names))

            if len(book_ids) >= len(book_names):
                # More (or equal) IDs than names: most likely several
                # physical copies of the same title, so reuse the last
                # name to cover the extra IDs.
                ids = book_ids
                names = book_names + [book_names[-1]] * (target_len - len(book_names))
            else:
                # Fewer IDs than names: the names are probably distinct
                # titles, so it would be wrong to guess which one the lone
                # ID belongs to. Leave the extra name(s) with a blank ID
                # instead of duplicating it.
                ids = book_ids + [""] * (target_len - len(book_ids))
                names = book_names

            if len(book_ids) != len(book_names):
                log.add(
                    "offline_library",
                    "review",
                    row["Excel Row"],
                    row["Sl.No"],
                    row["ID NO"],
                    row["Name of the Student"],
                    f"Book ID list ({len(book_ids)} item(s)) and Book Name list "
                    f"({len(book_names)} item(s)) don't match up - split into "
                    f"{target_len} book record(s); please verify the Book ID "
                    f"assigned to each",
                    f"Book ID='{raw_book_id}' | Reference Book='{raw_book_name}'",
                )

            for bid, bname in zip(ids, names):
                add_record(bid, bname)

    return pd.DataFrame(
        records,
        columns=[
            "Serial No.",
            "Date",
            "Student ID",
            "Student Name",
            "Book ID",
            "Book Name",
        ],
    )


def build_digital_class(df: pd.DataFrame, log: ErrorLog) -> pd.DataFrame:
    """One row per Digital Class session.

    Some Digital Class cells have a headcount tacked onto the end, e.g.
    'Reasoning Class(23)' or 'Current Affairs (Feb)(23)' - that trailing
    '(<number>)' is how many students were in the class, not part of the
    class name, so it's split off into its own 'Student Count' column.
    What's left of the name is then run through common.Canonicalizer so
    near-duplicate spellings of the same class (typos, casing, 'Class'
    suffix, extra spacing - e.g. 'Current Affairs' / 'Current Afairs' /
    'Current Affiars') collapse onto one canonical name instead of each
    becoming its own value downstream.
    """
    mask = df["Digital Class"] != ""
    sub = df.loc[mask].copy()

    # Canonicalizer logs corrections/review-flags through these callbacks;
    # `current` holds the row currently being processed so the callbacks
    # (which only receive a category/message) can still log full row
    # context (excel row, student id/name) via ErrorLog.add.
    current = {}

    def log_auto(category, message):
        row = current["row"]
        log.add(
            "digital_class",
            "corrected",
            row["Excel Row"],
            row["Sl.No"],
            row["ID NO"],
            row["Name of the Student"],
            message,
        )

    def log_review(message):
        row = current["row"]
        log.add(
            "digital_class",
            "review",
            row["Excel Row"],
            row["Sl.No"],
            row["ID NO"],
            row["Name of the Student"],
            message,
        )

    canonicalizer = Canonicalizer(log_auto, log_review, category="Digital Class name")

    class_names = []
    student_counts = []

    for idx, row in sub.iterrows():
        current["row"] = row
        raw = row["Digital Class"]

        count = ""
        name_part = raw
        m = CLASS_COUNT_SUFFIX.search(raw)
        if m:
            count = m.group(1)
            name_part = raw[: m.start()].strip()
            log.add(
                "digital_class",
                "corrected",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                f"Trailing '({count})' removed from Digital Class value - this is a "
                f"student headcount for the class, not part of the class name; moved "
                f"to a separate 'Student Count' column",
                raw,
            )

        if name_part == "":
            # Nothing left but the headcount - no class name to work with.
            log.add(
                "digital_class",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Digital Class value was only a headcount in parentheses, with no "
                "class name - cannot be loaded until fixed",
                raw,
            )
            class_names.append("")
            student_counts.append(count)
            continue

        if name_part.isdigit():
            log.add(
                "digital_class",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Digital Class value is purely numeric, which doesn't look like a class "
                "name - please verify",
                name_part,
            )
            class_names.append(name_part)
            student_counts.append(count)
            continue

        canonical = canonicalizer.canonicalize(
            name_part, context=f"Excel row {row['Excel Row']}"
        )
        class_names.append(canonical)
        student_counts.append(count)

    out = pd.DataFrame(
        {
            "Serial No.": sub["Sl.No"],
            "Date": sub["Date_clean"]
            .dt.strftime("%d-%m-%Y")
            .where(sub["Date_clean"].notna(), sub["Date"]),
            "Student ID": sub["ID NO"],
            "Student Name": sub["Name of the Student"],
            "Class Name": class_names,
            "Student Count": student_counts,
        }
    )
    return out.reset_index(drop=True)


def build_offline_exam(df: pd.DataFrame, log: ErrorLog) -> pd.DataFrame:
    """One row per (student, date, exam topic) taken from the 'Offline Exam'
    column. The daily activity log records which student sat which exam on
    which day, but never a score -- load_offline_exam.py runs after the
    marks register and flags any sitting that the register never scored for
    manual review (exam_marks.marks_obtained is NOT NULL, so scoreless
    sittings are never inserted). Bare-date topics (a stray column-shift)
    are rejected at load time by the shared topic canonicalizer, not here."""
    mask = df["Offline Exam"] != ""
    sub = df.loc[mask].copy()
    records = []
    for idx, row in sub.iterrows():
        topic = collapse(row["Offline Exam"])
        if not topic:
            continue
        records.append(
            {
                "Serial No.": row["Sl.No"],
                "Date": (
                    row["Date_clean"].strftime("%d-%m-%Y")
                    if pd.notna(row["Date_clean"])
                    else row["Date"]
                ),
                "Student ID": row["ID NO"],
                "Student Name": row["Name of the Student"],
                "Exam Name": topic,
            }
        )
    return pd.DataFrame(
        records,
        columns=[
            "Serial No.",
            "Date",
            "Student ID",
            "Student Name",
            "Exam Name",
        ],
    )


def build_attendance(df: pd.DataFrame, log: ErrorLog) -> pd.DataFrame:
    sub = df.copy()

    clean_genders = []
    for idx, row in sub.iterrows():
        g = clean_gender(row["Gender"])
        if row["Gender"] != "" and g == "":
            log.add(
                "attendance",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Unrecognized Gender value",
                row["Gender"],
            )
        elif row["Gender"] == "":
            log.add(
                "attendance",
                "review",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                "Missing Gender",
            )
        elif g != row["Gender"]:
            log.add(
                "attendance",
                "corrected",
                row["Excel Row"],
                row["Sl.No"],
                row["ID NO"],
                row["Name of the Student"],
                f"Gender value normalized from '{row['Gender']}' to '{g}' (misspelling corrected)",
                row["Gender"],
            )
        clean_genders.append(g)
    sub["Gender_clean"] = clean_genders

    for idx, row in sub.iterrows():
        for label, col in (("In Time", "IN"), ("Out Time", "OUT")):
            val = row[col]
            if val == "":
                log.add(
                    "attendance",
                    "review",
                    row["Excel Row"],
                    row["Sl.No"],
                    row["ID NO"],
                    row["Name of the Student"],
                    f"Missing {label}",
                )
            elif not TIME_RE.match(val):
                log.add(
                    "attendance",
                    "review",
                    row["Excel Row"],
                    row["Sl.No"],
                    row["ID NO"],
                    row["Name of the Student"],
                    f"{label} is not in HH:MM format",
                    val,
                )

        if TIME_RE.match(row["IN"]) and TIME_RE.match(row["OUT"]):
            t_in = datetime.strptime(row["IN"], "%H:%M")
            t_out = datetime.strptime(row["OUT"], "%H:%M")
            if t_out < t_in:
                log.add(
                    "attendance",
                    "review",
                    row["Excel Row"],
                    row["Sl.No"],
                    row["ID NO"],
                    row["Name of the Student"],
                    "Out Time is earlier than In Time",
                    f"IN={row['IN']} OUT={row['OUT']}",
                )

    out = pd.DataFrame(
        {
            "Serial No.": sub["Sl.No"],
            "Date": sub["Date_clean"]
            .dt.strftime("%d-%m-%Y")
            .where(sub["Date_clean"].notna(), sub["Date"]),
            "Student ID": sub["ID NO"],
            "Student Name": sub["Name of the Student"],
            "Gender": sub["Gender_clean"],
            "In Time": sub["IN"],
            "Out Time": sub["OUT"],
        }
    )
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------


def main():
    if len(sys.argv) < 2:
        print("Usage: python clean_student_data.py <input.csv> [base_folder]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    # Base directory the project's per-domain folders live under. Default is
    # this script's own folder -- the project root, sibling to
    # digital_library/, offline_library/, coaching/, attendance/ -- so each
    # cleaned CSV lands directly next to the loader that consumes it. Pass
    # an explicit folder as the 2nd arg to redirect everything under a
    # separate test/staging tree instead (the same section subfolders are
    # still created under it).
    base_dir = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent
    )

    section_dirs = {
        section: base_dir / folder
        for section, folder in ErrorLog.SECTION_FOLDERS.items()
    }
    for d in section_dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    log = ErrorLog()

    print(f"Loading {input_path} ...")
    df = load_raw(input_path)
    total_rows = len(df)
    print(f"  {total_rows} data rows found (after removing blank padding rows).")

    print("Validating Student ID / Student Name ...")
    df = validate_core_fields(df, log)
    usable_rows = len(df)
    print(f"  {usable_rows} rows remain usable.")

    print("Cleaning dates ...")
    df = fix_dates(df, log)

    print("Cleaning times ...")
    df = fix_times(df, log)

    print("Correcting 12-hour clock slips / operating hours ...")
    df = fix_operating_hours(df, log)

    print("Building sections ...")
    digital_library = build_digital_library(df, log)
    offline_library = build_offline_library(df, log)
    digital_class = build_digital_class(df, log)
    attendance = build_attendance(df, log)
    offline_exam = build_offline_exam(df, log)

    digital_library.to_csv(
        section_dirs["digital_library"] / "digital_library.csv",
        index=False,
        quoting=csv.QUOTE_MINIMAL,
    )
    offline_library.to_csv(
        section_dirs["offline_library"] / "offline_library.csv",
        index=False,
        quoting=csv.QUOTE_MINIMAL,
    )
    digital_class.to_csv(
        section_dirs["digital_class"] / "digital_class.csv",
        index=False,
        quoting=csv.QUOTE_MINIMAL,
    )
    attendance.to_csv(
        section_dirs["attendance"] / "attendance.csv",
        index=False,
        quoting=csv.QUOTE_MINIMAL,
    )
    offline_exam.to_csv(
        section_dirs["offline_exam"] / "offline_exam.csv",
        index=False,
        quoting=csv.QUOTE_MINIMAL,
    )

    log.write_all(base_dir, str(input_path))

    review_counts = {key: 0 for key, _ in ErrorLog.SECTIONS}
    corrected_counts = {key: 0 for key, _ in ErrorLog.SECTIONS}
    for entry in log.rows:
        if entry["status"] == "review":
            review_counts[entry["section"]] += 1
        else:
            corrected_counts[entry["section"]] += 1

    total_review = sum(review_counts.values())
    total_corrected = sum(corrected_counts.values())

    print()
    print("Done. Rows written:")
    print(
        f"  Digital Library ({section_dirs['digital_library'] / 'digital_library.csv'}): {len(digital_library)}"
    )
    print(
        f"  Offline Library ({section_dirs['offline_library'] / 'offline_library.csv'}): {len(offline_library)}"
    )
    print(
        f"  Digital Class   ({section_dirs['digital_class'] / 'digital_class.csv'}): {len(digital_class)}"
    )
    print(
        f"  Attendance      ({section_dirs['attendance'] / 'attendance.csv'}): {len(attendance)}"
    )
    print(
        f"  Offline Exam    ({section_dirs['offline_exam'] / 'offline_exam.csv'}): {len(offline_exam)}"
    )
    print()
    print(f"Needs manual review ({total_review} total):")
    for key, title in ErrorLog.SECTIONS:
        folder = ErrorLog.SECTION_FOLDERS.get(key)
        loc = (base_dir / folder) if folder else base_dir
        print(f"  {review_counts[key]:>5}  {loc / f'error_log_{key}.log'}  ({title})")
    print()
    print(f"Auto-corrected by the script ({total_corrected} total):")
    for key, title in ErrorLog.SECTIONS:
        folder = ErrorLog.SECTION_FOLDERS.get(key)
        loc = (base_dir / folder) if folder else base_dir
        print(
            f"  {corrected_counts[key]:>5}  {loc / f'corrections_log_{key}.log'}  ({title})"
        )


if __name__ == "__main__":
    main()
