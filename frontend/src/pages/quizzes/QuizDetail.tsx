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
import { useQuiz, useScoresForQuiz, useAddQuizScore, useUpdateQuizScore, useDeleteQuizScore } from "../../api/quizzes";
import { extractErrorMessage } from "../../api/client";
import { formatDate } from "../../lib/format";
import type { Student, QuizScore } from "../../api/types";

export function QuizDetail() {
  const { quizId } = useParams();
  const navigate = useNavigate();
  const id = Number(quizId);
  const { data: quiz, isLoading, isError, error } = useQuiz(id);
  const scores = useScoresForQuiz(id);

  const [addOpen, setAddOpen] = useState(false);
  const [editingScore, setEditingScore] = useState<QuizScore | undefined>(undefined);
  const [deletingScore, setDeletingScore] = useState<QuizScore | undefined>(undefined);
  const deleteMutation = useDeleteQuizScore(id);

  async function handleDelete() {
    if (!deletingScore) return;
    try {
      await deleteMutation.mutateAsync(deletingScore.score_id);
      toast.success("Score removed");
      setDeletingScore(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  if (isLoading) return <Spinner label="Loading quiz…" />;
  if (isError || !quiz) return <ErrorBanner message={extractErrorMessage(error)} />;

  return (
    <div>
      <button onClick={() => navigate("/quizzes")} className="mb-4 flex items-center gap-1.5 text-sm text-slate hover:text-ink">
        <ArrowLeft size={15} /> Back to quizzes
      </button>

      <PageHeader
        eyebrow={quiz.subject ?? "Quiz"}
        title={quiz.quiz_name}
        description={`${formatDate(quiz.quiz_date)} · Max marks ${quiz.max_marks}`}
        action={
          <Button variant="primary" onClick={() => setAddOpen(true)}>
            <Plus size={16} /> Record score
          </Button>
        }
      />

      {scores.isLoading && <Spinner label="Loading scores…" />}
      {scores.isError && <ErrorBanner message={extractErrorMessage(scores.error)} />}
      {scores.data && scores.data.length === 0 && (
        <EmptyState title="No scores recorded yet" description="Record a student's score for this quiz." />
      )}

      {scores.data && scores.data.length > 0 && (
        <Table>
          <Thead>
            <Th>Student</Th>
            <Th>Score</Th>
            <Th>Remarks</Th>
            <Th className="text-right">Actions</Th>
          </Thead>
          <tbody>
            {scores.data.map((s) => (
              <Tr key={s.score_id}>
                <Td className="font-mono text-xs">{s.student_id}</Td>
                <Td className="font-medium">
                  {s.score} / {quiz.max_marks}
                </Td>
                <Td className="text-slate">{s.remarks ?? "—"}</Td>
                <Td>
                  <div className="flex justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => setEditingScore(s)}>
                      <Pencil size={14} />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setDeletingScore(s)}>
                      <Trash2 size={14} className="text-rust" />
                    </Button>
                  </div>
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}

      <AddScoreModal open={addOpen} onClose={() => setAddOpen(false)} quizId={id} maxMarks={quiz.max_marks} />
      {editingScore && (
        <EditScoreModal
          open={Boolean(editingScore)}
          onClose={() => setEditingScore(undefined)}
          quizId={id}
          score={editingScore}
          maxMarks={quiz.max_marks}
        />
      )}

      <ConfirmDialog
        open={Boolean(deletingScore)}
        onClose={() => setDeletingScore(undefined)}
        onConfirm={handleDelete}
        title="Delete score"
        message="This removes the recorded score for this student."
        pending={deleteMutation.isPending}
      />
    </div>
  );
}

function AddScoreModal({
  open,
  onClose,
  quizId,
  maxMarks,
}: {
  open: boolean;
  onClose: () => void;
  quizId: number;
  maxMarks: number;
}) {
  const [student, setStudent] = useState<Student | null>(null);
  const [score, setScore] = useState("");
  const [remarks, setRemarks] = useState("");
  const [error, setError] = useState("");
  const addMutation = useAddQuizScore(quizId);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!student) {
      setError("Pick a student.");
      return;
    }
    if (score === "" || Number(score) < 0) {
      setError("Enter a valid score.");
      return;
    }
    try {
      await addMutation.mutateAsync({
        student_id: student.student_id,
        score: Number(score),
        remarks: remarks || null,
      });
      toast.success(`Recorded score for ${student.name}`);
      onClose();
      setStudent(null);
      setScore("");
      setRemarks("");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Record score" subtitle={`Out of ${maxMarks}`}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Student" required>
          <StudentPicker value={student} onChange={setStudent} activeOnly={false} />
        </Field>
        <Field label="Score" required>
          <Input type="number" min="0" max={maxMarks} step="0.5" value={score} onChange={(e) => setScore(e.target.value)} />
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

function EditScoreModal({
  open,
  onClose,
  quizId,
  score,
  maxMarks,
}: {
  open: boolean;
  onClose: () => void;
  quizId: number;
  score: QuizScore;
  maxMarks: number;
}) {
  const [scoreValue, setScoreValue] = useState(String(score.score));
  const [remarks, setRemarks] = useState(score.remarks ?? "");
  const [error, setError] = useState("");
  const updateMutation = useUpdateQuizScore(quizId, score.score_id);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (scoreValue === "" || Number(scoreValue) < 0) {
      setError("Enter a valid score.");
      return;
    }
    try {
      await updateMutation.mutateAsync({
        score: Number(scoreValue),
        remarks: remarks || null,
      });
      toast.success("Score updated");
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Edit score" subtitle={`Student ${score.student_id} · Out of ${maxMarks}`}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Score" required>
          <Input type="number" min="0" max={maxMarks} step="0.5" value={scoreValue} onChange={(e) => setScoreValue(e.target.value)} />
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
