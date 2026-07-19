import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { ArrowLeft, Plus, Pencil, Trash2 } from "lucide-react";
import { Spinner, ErrorBanner, EmptyState, PageHeader } from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
import { Field, Input, Textarea } from "../../components/ui/Form";
import { Modal } from "../../components/ui/Modal";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { StudentPicker } from "../../components/ui/StudentPicker";
import { useExam, useMarksForExam, useAddExamMark, useUpdateExamMark, useDeleteExamMark } from "../../api/exams";
import { extractErrorMessage } from "../../api/client";
import { formatDate } from "../../lib/format";
import type { Student, ExamMark } from "../../api/types";

export function ExamDetail() {
  const { examId } = useParams();
  const navigate = useNavigate();
  const id = Number(examId);
  const { data: exam, isLoading, isError, error } = useExam(id);
  const marks = useMarksForExam(id);

  const [addOpen, setAddOpen] = useState(false);
  const [editingMark, setEditingMark] = useState<ExamMark | undefined>(undefined);
  const [deletingMark, setDeletingMark] = useState<ExamMark | undefined>(undefined);
  const deleteMutation = useDeleteExamMark(id);

  async function handleDelete() {
    if (!deletingMark) return;
    try {
      await deleteMutation.mutateAsync(deletingMark.mark_id);
      toast.success("Mark removed");
      setDeletingMark(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  if (isLoading) return <Spinner label="Loading exam…" />;
  if (isError || !exam) return <ErrorBanner message={extractErrorMessage(error)} />;

  return (
    <div>
      <button onClick={() => navigate("/exams")} className="mb-4 flex items-center gap-1.5 text-sm text-slate hover:text-ink">
        <ArrowLeft size={15} /> Back to exams
      </button>

      <PageHeader
        eyebrow={exam.subject ?? "Exam"}
        title={exam.exam_name}
        description={`${formatDate(exam.exam_date)} · Max marks ${exam.max_marks}`}
        action={
          <Button variant="primary" onClick={() => setAddOpen(true)}>
            <Plus size={16} /> Record marks
          </Button>
        }
      />

      {marks.isLoading && <Spinner label="Loading marks…" />}
      {marks.isError && <ErrorBanner message={extractErrorMessage(marks.error)} />}
      {marks.data && marks.data.length === 0 && (
        <EmptyState title="No marks recorded yet" description="Record a student's marks for this exam." />
      )}

      {marks.data && marks.data.length > 0 && (
        <Table>
          <Thead>
            <Th>Student</Th>
            <Th>Marks</Th>
            <Th>Remarks</Th>
            <Th className="text-right">Actions</Th>
          </Thead>
          <tbody>
            {marks.data.map((m) => (
              <Tr key={m.mark_id}>
                <Td className="font-mono text-xs">{m.student_id}</Td>
                <Td className="font-medium">
                  {m.marks_obtained} / {exam.max_marks}
                </Td>
                <Td className="text-slate">{m.remarks ?? "—"}</Td>
                <Td>
                  <div className="flex justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => setEditingMark(m)}>
                      <Pencil size={14} />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setDeletingMark(m)}>
                      <Trash2 size={14} className="text-rust" />
                    </Button>
                  </div>
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}

      <AddMarkModal open={addOpen} onClose={() => setAddOpen(false)} examId={id} maxMarks={exam.max_marks} />
      {editingMark && (
        <EditMarkModal
          open={Boolean(editingMark)}
          onClose={() => setEditingMark(undefined)}
          examId={id}
          mark={editingMark}
          maxMarks={exam.max_marks}
        />
      )}

      <ConfirmDialog
        open={Boolean(deletingMark)}
        onClose={() => setDeletingMark(undefined)}
        onConfirm={handleDelete}
        title="Delete mark"
        message="This removes the recorded mark for this student."
        pending={deleteMutation.isPending}
      />
    </div>
  );
}

function AddMarkModal({
  open,
  onClose,
  examId,
  maxMarks,
}: {
  open: boolean;
  onClose: () => void;
  examId: number;
  maxMarks: number;
}) {
  const [student, setStudent] = useState<Student | null>(null);
  const [marksObtained, setMarksObtained] = useState("");
  const [remarks, setRemarks] = useState("");
  const [error, setError] = useState("");
  const addMutation = useAddExamMark(examId);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!student) {
      setError("Pick a student.");
      return;
    }
    if (marksObtained === "" || Number(marksObtained) < 0) {
      setError("Enter valid marks.");
      return;
    }
    try {
      await addMutation.mutateAsync({
        student_id: student.student_id,
        marks_obtained: Number(marksObtained),
        remarks: remarks || null,
      });
      toast.success(`Recorded marks for ${student.name}`);
      onClose();
      setStudent(null);
      setMarksObtained("");
      setRemarks("");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Record marks" subtitle={`Out of ${maxMarks}`}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Student" required>
          <StudentPicker value={student} onChange={setStudent} activeOnly={false} />
        </Field>
        <Field label="Marks obtained" required>
          <Input type="number" min="0" max={maxMarks} step="0.5" value={marksObtained} onChange={(e) => setMarksObtained(e.target.value)} />
        </Field>
        <Field label="Remarks">
          <Textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} />
        </Field>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={addMutation.isPending}>
            {addMutation.isPending ? "Saving…" : "Record"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function EditMarkModal({
  open,
  onClose,
  examId,
  mark,
  maxMarks,
}: {
  open: boolean;
  onClose: () => void;
  examId: number;
  mark: ExamMark;
  maxMarks: number;
}) {
  const [marksObtained, setMarksObtained] = useState(String(mark.marks_obtained));
  const [remarks, setRemarks] = useState(mark.remarks ?? "");
  const [error, setError] = useState("");
  const updateMutation = useUpdateExamMark(examId, mark.mark_id);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (marksObtained === "" || Number(marksObtained) < 0) {
      setError("Enter valid marks.");
      return;
    }
    try {
      await updateMutation.mutateAsync({
        marks_obtained: Number(marksObtained),
        remarks: remarks || null,
      });
      toast.success("Mark updated");
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Edit marks" subtitle={`Student ${mark.student_id} · Out of ${maxMarks}`}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Marks obtained" required>
          <Input type="number" min="0" max={maxMarks} step="0.5" value={marksObtained} onChange={(e) => setMarksObtained(e.target.value)} />
        </Field>
        <Field label="Remarks">
          <Textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} />
        </Field>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={updateMutation.isPending}>
            {updateMutation.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
