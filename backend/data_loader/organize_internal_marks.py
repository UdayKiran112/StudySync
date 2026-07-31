import csv
import re
import os

SRC = "internal_marks.csv"
OUT = "./outputs/internal_marks_organized.csv"


def is_int(s):
    return bool(re.fullmatch(r"\d+", s.strip()))


rows_out = []
cur_exam_name = ""
cur_exam_date = ""
cur_max_marks = ""
anomalies = []

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

        if not c0 and not c1 and (c2 or c3 or c4 or c5):
            cur_exam_name = c2 if c2 else cur_exam_name
            cur_exam_date = c3 if c3 else cur_exam_date
            if c4:
                cur_max_marks = c4
            elif c5:
                cur_max_marks = c5
            continue

        if c0 or c1:
            name = c1
            student_id = ""
            marks = c5

            if c2 and not is_int(c2):
                cur_exam_name = c2
                if c3:
                    cur_exam_date = c3
                if c4:
                    cur_max_marks = c4
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

        anomalies.append((lineno, row))

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
print("Anomalous rows (unparsed):", len(anomalies))
for a in anomalies[:20]:
    print(a)
