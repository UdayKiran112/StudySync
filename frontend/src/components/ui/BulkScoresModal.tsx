import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Modal } from "./Modal";
import { Button } from "./Button";
import { Field, Input } from "./Form";
import { StudentPicker } from "./StudentPicker";
import type { Student } from "../../api/types";

export interface BulkSaveRow {
  studentId: number;
  value: number;
  remarks: string | null;
  /** Present when this row edits an existing record instead of adding one. */
  recordId?: number;
}

export interface ExistingScoreRow {
  student_id: number;
  recordId: number;
  value: number;
  remarks: string | null;
}

interface Row {
  key: string;
  student: Student | null;
  existingStudentId?: number;
  recordId?: number;
  value: string;
  remarks: string;
}

export function BulkScoresModal({
  open,
  onClose,
  title,
  label,
  maxMarks,
  existing,
  onSave,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  label: string;
  maxMarks: number;
  existing: ExistingScoreRow[];
  /** Save every row in one pass; throws with a message on failure. */
  onSave: (rows: BulkSaveRow[]) => Promise<void>;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError("");
    setPending(false);
    setRows([
      ...existing.map((e) => ({
        key: `existing-${e.recordId}`,
        student: null,
        existingStudentId: e.student_id,
        recordId: e.recordId,
        value: String(e.value),
        remarks: e.remarks ?? "",
      })),
      { key: "new-1", student: null, value: "", remarks: "" },
    ]);
  }, [open, existing]);

  function updateRow(key: string, patch: Partial<Row>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function removeRow(key: string) {
    setRows((prev) => prev.filter((r) => r.key !== key));
  }

  function addRow() {
    setRows((prev) => [
      ...prev,
      { key: `new-${Date.now()}`, student: null, value: "", remarks: "" },
    ]);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    const submissions: BulkSaveRow[] = [];
    for (const row of rows) {
      const studentId = row.student?.student_id ?? row.existingStudentId;
      if (!studentId) continue; // skip rows with no student picked
      if (row.value === "" || Number(row.value) < 0 || Number(row.value) > maxMarks) {
        setError(
          `Marks for student ${studentId} must be between 0 and ${maxMarks}.`,
        );
        return;
      }
      submissions.push({
        studentId,
        value: Number(row.value),
        remarks: row.remarks.trim() || null,
        recordId: row.recordId,
      });
    }

    if (submissions.length === 0) {
      setError("Add at least one student and enter their marks first.");
      return;
    }

    setPending(true);
    try {
      await onSave(submissions);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong while saving.");
      setPending(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={title} subtitle={`Out of ${maxMarks}`} width="lg">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="rounded-md border border-border bg-paper-dim px-3 py-2 text-sm text-slate">
          Add one row per student, then save everything at once. Rows for
          students who already have a {label.toLowerCase()} are updated in
          place.
        </div>

        <div className="space-y-3">
          {rows.map((row) => (
            <div key={row.key} className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(200px,1fr)_110px_1fr_auto] sm:items-end">
              <Field label="Student">
                {row.student ? (
                  <div className="flex h-9 items-center rounded-md border border-border bg-paper-dim px-3 text-sm text-ink">
                    {row.student.student_id} · {row.student.name}
                  </div>
                ) : (
                  <StudentPicker
                    value={row.student}
                    onChange={(s) => updateRow(row.key, { student: s })}
                    activeOnly={false}
                    autoFocus
                  />
                )}
              </Field>
              <Field label={label} required>
                <Input
                  type="number"
                  min="0"
                  max={maxMarks}
                  step="0.5"
                  value={row.value}
                  onChange={(e) => updateRow(row.key, { value: e.target.value })}
                  placeholder={`0–${maxMarks}`}
                />
              </Field>
              <Field label="Remarks">
                <Input
                  value={row.remarks}
                  onChange={(e) => updateRow(row.key, { remarks: e.target.value })}
                  placeholder="Optional"
                />
              </Field>
              <div className="flex justify-end">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label="Remove row"
                  onClick={() => removeRow(row.key)}
                >
                  <Trash2 size={14} className="text-rust" />
                </Button>
              </div>
            </div>
          ))}
        </div>

        <Button type="button" variant="secondary" size="sm" onClick={addRow}>
          <Plus size={14} /> Add student
        </Button>

        {error && <p className="text-sm text-rust">{error}</p>}

        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={pending}>
            {pending ? "Saving…" : "Save all"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
