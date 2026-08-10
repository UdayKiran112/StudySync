import csv, re, os
from collections import defaultdict

import common

SRC = "internal_marks.csv"
OUT = "./marks/internal_marks_organized.csv"

NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def is_int(s):
    return bool(re.fullmatch(r"\d+", s.strip()))


def has_letters(s):
    return bool(re.search(r"[A-Za-z]", s))


DATE_RE = re.compile(r"^\d{1,2}[.\-/ ]\d{1,2}[.\-/ ]\d{2,4}$")


def looks_like_date(s):
    return bool(DATE_RE.match(s))


def extract_number(s):
    """Pull the first numeric token out of a cell, tolerating stray text
    around it (e.g. '25 Marks' -> '25'). Returns None if no digits at all."""
    m = NUM_RE.search(s)
    return m.group(0) if m else None


rows_out = []
cur_exam_name = ""
cur_exam_date = ""
cur_max_marks = ""
anomalies = []
max_marks_corrections = []
swap_corrections = []


# One-off data-entry typos in the raw register where the recorded score
# exceeds the block's max marks. Keyed by (Date of Exam, Name of Exam,
# Marks Obtained) -- enough to uniquely identify the row:
#   - Gowthami A 24.10.2025 A&R Exam '4033' = '4.33' (numpad '.' typed as
#     the adjacent '0' key);
#   - Vasudeva Rao D 14.07.2022 Reasoning '166' = '16.6' (decimal point
#     dropped);
#   - Janardhana D 21.02.2026 Arithmetic '62.66' = '26.66' (leading 6/2
#     transposed).
MARKS_CORRECTIONS = {
    ("24.10.2025", "A&R Exam", "4033"): "4.33",
    ("14.07.2022", "Reasoning", "166"): "16.6",
    ("21.02.2026", "Arithmetic", "62.66"): "26.66",
}


def resolve_max_marks(lineno, c4, c5):
    """Max Marks is normally in c4 (the column right after Date), but in a
    number of blocks the Excel export leaves c4 blank and the value lands
    one column over in c5 (Marks Obtained) instead. Try c4 first, then
    fall back to c5, and in both cases pull out the leading number in
    case of stray text like '25 Marks'. Returns (new_max_or_None, note_or_None).
    """
    for col_name, val in (("Max Marks col", c4), ("Marks Obtained col", c5)):
        if not val:
            continue
        num = extract_number(val)
        if num is None:
            continue
        if col_name == "Marks Obtained col":
            note = f"line {lineno}: Max Marks blank, found {num!r} in {col_name} (raw {val!r}) -> used as max marks"
        elif num != val:
            note = f"line {lineno}: Max Marks col had {val!r}, extracted {num!r} -> used as max marks"
        else:
            note = None  # clean, expected case -- not worth logging
        return num, note
    return None, None


with open(SRC, newline="", encoding="utf-8-sig") as f:
    reader = csv.reader(f)
    for lineno, row in enumerate(reader, start=1):
        row = row + [""] * (6 - len(row)) if len(row) < 6 else row
        c0, c1, c2, c3, c4, c5 = [row[i].strip() for i in range(6)]

        if not any([c0, c1, c2, c3, c4, c5]):
            continue

        # skip the original literal column-header labels row (e.g. "Sl No, Name..., Date, ...")
        if c0.lower().replace(".", "").strip() in ("sl no", "slno", "s no"):
            continue

        # Exam name / date occasionally land in each other's column too
        # (e.g. '19.12.2025' typed into Name of the Exam, 'A&R Exam' typed
        # into Date) -- swap them back when one looks like a date and the
        # other looks like a topic.
        if (
            c2
            and c3
            and looks_like_date(c2)
            and not has_letters(c2)
            and has_letters(c3)
        ):
            c2, c3 = c3, c2
            swap_corrections.append(
                f"line {lineno}: topic/date looked swapped -- corrected to exam={c2!r}, date={c3!r}"
            )

        if not c0 and not c1:
            # A genuine block-header row establishes a new exam: it names
            # the topic and/or gives a date. A row with neither -- just a
            # stray number sitting in Max Marks / Marks Obtained with
            # nothing else -- is export noise, not a header, and must NOT
            # be allowed to clobber the max marks carried over from the
            # real header above it.
            if not c2 and not c3:
                if c4 or c5:
                    anomalies.append(
                        (lineno, row, "stray value with no topic/date -- ignored")
                    )
                continue

            if c2:
                if has_letters(c2):
                    cur_exam_name = c2
                else:
                    anomalies.append(
                        (
                            lineno,
                            row,
                            f"exam name {c2!r} has no letters -- ignored, kept {cur_exam_name!r}",
                        )
                    )

            if c3:
                cur_exam_date = c3

            new_max, note = resolve_max_marks(lineno, c4, c5)
            if new_max:
                cur_max_marks = new_max
                if note:
                    max_marks_corrections.append(note)
            elif c4 or c5:
                anomalies.append(
                    (lineno, row, "max marks present but not numeric -- kept previous")
                )
            continue

        if c0 or c1:
            name = c1
            student_id = ""
            marks = c5

            if c2 and not is_int(c2):
                if has_letters(c2):
                    cur_exam_name = c2
                    if c3:
                        cur_exam_date = c3
                    if c4:
                        new_max, note = resolve_max_marks(lineno, c4, "")
                        if new_max:
                            cur_max_marks = new_max
                            if note:
                                max_marks_corrections.append(note)
                else:
                    anomalies.append(
                        (
                            lineno,
                            row,
                            f"exam name {c2!r} has no letters -- ignored, kept {cur_exam_name!r}",
                        )
                    )
                marks = c5
            elif c2 and is_int(c2):
                student_id = c2

            rows_out.append(
                {
                    "id": student_id,
                    "date": cur_exam_date,
                    "exam": cur_exam_name,
                    "marks": marks,
                    "max_marks": cur_max_marks,
                    "name": name,
                    "src_line": lineno,
                }
            )
            continue

        anomalies.append((lineno, row, "unrecognized row shape"))

marks_corrections_applied = []
for r in rows_out:
    key = (r["date"], r["exam"], r["marks"])
    if key in MARKS_CORRECTIONS:
        corrected = MARKS_CORRECTIONS[key]
        marks_corrections_applied.append(
            f"{r['name']!r} {r['date']} {r['exam']!r}: marks {r['marks']!r} "
            f"(max {r['max_marks']!r}) -> corrected to {corrected!r}"
        )
        r["marks"] = corrected

# ---------------------------------------------------------------------------
# Marks validation pass (post-organization, before the CSV is written).
#
# Every row is now one resolved (student, exam, marks, max_marks) fact, so
# this is the right place to enforce the pipeline's hard invariant: a mark
# must never exceed its exam's max marks. Rows that violate it are handled
# in increasing order of confidence:
#
#   1. COLON-FOR-DECIMAL TYPOS -- the same ':' instead of '.' slip that the
#      date/time columns contain ("18:50" meant 18.5). Only applied to a
#      value that is exactly `digits:1-2 digits` and whose corrected numeric
#      value is still a plausible, in-range score.
#   2. BLOCK-LEVEL MAX-MARKS TYPOS -- when 2+ students in one exam block
#      exceed the recorded max, the max itself is a data-entry error, not
#      the students' marks (e.g. a "Constable" block recorded as 100 max
#      where every other Constable block in the register is 200). The max is
#      corrected to the topic's dominant max only when that dominant max is
#      >= the block's highest mark (i.e. it actually fits the block).
#   3. UNRESOLVABLE OVER-MAX ROWS -- a single mark above the max (a
#      misplaced pasted value, a mis-typed score, or a max that can't be
#      cross-checked) has NO confident correction. It is left untouched in
#      the CSV so load_exam_marks.py can flag it for manual review and
#      deliberately NOT insert it -- a fabricated value is worse than a gap.
# ---------------------------------------------------------------------------

# A marks cell that is really a time (the ':' key used instead of '.'),
# e.g. '18:50' -> 18.5. Everything else non-numeric is left alone and is
# skipped+flagged by the loader.
TIME_LIKE_MARKS_RE = re.compile(r"^\d+:\d{1,2}$")

colon_decimal_corrections = []
for r in rows_out:
    m = r["marks"].strip()
    if TIME_LIKE_MARKS_RE.match(m):
        recovered = m.replace(":", ".")
        try:
            value = float(recovered)
        except ValueError:
            continue
        if value >= 0 and (not r["max_marks"] or value <= float(r["max_marks"])):
            colon_decimal_corrections.append(
                f"{r['name']!r} {r['date']} {r['exam']!r}: marks {m!r} read "
                f"as decimal {recovered!r} (colon-for-dot typo)"
            )
            r["marks"] = recovered

# Group rows into exam blocks by (canonical exam identity, date). Identity
# comes from common.exam_identity_key so 'Constable' / 'Constable G T' /
# 'Constable Grand Test' collapse onto one real exam for cross-exam checks.
blocks = defaultdict(list)
for r in rows_out:
    blocks[(common.exam_identity_key(r["exam"]), r["date"])].append(r)

# Most-common max marks for a canonical topic across ALL its blocks -- the
# scale the exam is normally run on (used to repair a mis-recorded block max).
topic_max_marks = defaultdict(list)
for (ident, _date), block in blocks.items():
    for r in block:
        if r["max_marks"]:
            topic_max_marks[ident].append(r["max_marks"])

def dominant_max(ident):
    if not topic_max_marks[ident]:
        return None
    counts = defaultdict(int)
    for m in topic_max_marks[ident]:
        counts[m] += 1
    best = max(counts.items(), key=lambda kv: kv[1])
    return best[0] if best[1] >= 2 else None

block_max_corrections = []
over_max_flagged = []
for (ident, date), block in blocks.items():
    maxes = {r["max_marks"] for r in block if r["max_marks"]}
    if not maxes:
        continue
    over = [r for r in block if r["marks"] and r["max_marks"] and float(r["marks"]) > float(r["max_marks"])]
    if len(over) >= 2:
        dom = dominant_max(ident)
        block_high = max(float(r["marks"]) for r in over)
        if dom is not None and float(dom) >= block_high and float(dom) not in {float(m) for m in maxes}:
            block_max_corrections.append(
                f"{date} {over[0]['exam']!r}: {len(over)} students exceed the "
                f"recorded max {sorted(maxes)} -- other {ident!r} blocks in this "
                f"register use {dom}; block max corrected to {dom} (fits the "
                f"block's highest mark {block_high:g})"
            )
            for r in block:
                if r["max_marks"]:
                    r["max_marks"] = dom
    elif len(over) == 1:
        over_max_flagged.append(
            f"{over[0]['name']!r} {date} {over[0]['exam']!r}: marks "
            f"{over[0]['marks']} > max {over[0]['max_marks']} -- no confident "
            f"correction; flagged for manual review and NOT loaded"
        )

os.makedirs("outputs", exist_ok=True)
with open(OUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(
        [
            "Sl.No",
            "ID",
            "Name of Student",
            "Date of Exam",
            "Name of Exam",
            "Marks Obtained",
            "Max Marks",
        ]
    )
    for i, r in enumerate(rows_out, start=1):
        writer.writerow(
            [i, r["id"], r["name"], r["date"], r["exam"], r["marks"], r["max_marks"]]
        )

print("Total student rows written:", len(rows_out))
print()
print("Marks corrections applied:", len(marks_corrections_applied))
for note in marks_corrections_applied:
    print(" ", note)
print()
print("Colon-for-decimal marks corrections:", len(colon_decimal_corrections))
for note in colon_decimal_corrections:
    print(" ", note)
print()
print("Block max-marks corrections:", len(block_max_corrections))
for note in block_max_corrections:
    print(" ", note)
print()
print("Unresolvable over-max rows (flagged, NOT loaded):", len(over_max_flagged))
for note in over_max_flagged:
    print(" ", note)
print()
print("Topic/date swap corrections:", len(swap_corrections))
for note in swap_corrections:
    print(" ", note)
print()
print("Max marks auto-corrections:", len(max_marks_corrections))
for note in max_marks_corrections:
    print(" ", note)
print()
print("Anomalous rows (unparsed / ignored):", len(anomalies))
for lineno, row, reason in anomalies[:30]:
    print(" ", lineno, row, "--", reason)
