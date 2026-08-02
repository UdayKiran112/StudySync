import csv, re, os

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
