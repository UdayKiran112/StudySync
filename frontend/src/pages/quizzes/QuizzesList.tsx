import { useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { Plus, Search, Pencil, Trash2 } from "lucide-react";
import {
  PageHeader,
  Spinner,
  ErrorBanner,
  EmptyState,
  Pagination,
} from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Input, Field } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Modal } from "../../components/ui/Modal";
import {
  useQuizzes,
  useCreateQuiz,
  useUpdateQuiz,
  useDeleteQuiz,
} from "../../api/quizzes";
import { extractErrorMessage } from "../../api/client";
import { formatDate, todayIso } from "../../lib/format";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import type { Quiz } from "../../api/types";

const LIMIT = 20;

export function QuizzesList() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Quiz | undefined>(undefined);
  const [deleting, setDeleting] = useState<Quiz | undefined>(undefined);

  const debouncedSearch = useDebouncedValue(search);
  const { data, isLoading, isError, error } = useQuizzes({
    search: debouncedSearch || undefined,
    limit: LIMIT,
    offset,
  });
  const deleteMutation = useDeleteQuiz();

  async function handleDelete() {
    if (!deleting) return;
    try {
      await deleteMutation.mutateAsync(deleting.quiz_id);
      toast.success(`Removed ${deleting.quiz_name}`);
      setDeleting(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Assessments"
        title="Quizzes"
        description="Manage the quiz catalog. Open a quiz to record student scores."
        action={
          <Button
            variant="primary"
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            <Plus size={16} /> Add quiz
          </Button>
        }
      />

      <div className="relative mb-4 max-w-sm">
        <Search
          size={15}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-light"
        />
        <Input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
          placeholder="Search by quiz name…"
          className="pl-9"
        />
      </div>

      {isLoading && <Spinner label="Loading quizzes…" />}
      {isError && <ErrorBanner message={extractErrorMessage(error)} />}
      {data && data.length === 0 && <EmptyState title="No quizzes found" />}

      {data && data.length > 0 && (
        <>
          <Table>
            <Thead>
              <Th>Quiz</Th>
              <Th>Subject</Th>
              <Th>Date</Th>
              <Th>Max marks</Th>
              <Th className="text-right">Actions</Th>
            </Thead>
            <tbody>
              {data.map((q) => (
                <Tr
                  key={q.quiz_id}
                  onClick={() => navigate(`/quizzes/${q.quiz_id}`)}
                >
                  <Td className="font-medium">{q.quiz_name}</Td>
                  <Td className="text-slate">{q.subject ?? "—"}</Td>
                  <Td className="text-slate">{formatDate(q.quiz_date)}</Td>
                  <Td className="font-mono text-xs">{q.max_marks}</Td>
                  <Td>
                    <div
                      className="flex justify-end gap-1"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditing(q);
                          setFormOpen(true);
                        }}
                      >
                        <Pencil size={14} />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeleting(q)}
                      >
                        <Trash2 size={14} className="text-rust" />
                      </Button>
                    </div>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
          <Pagination
            offset={offset}
            limit={LIMIT}
            count={data.length}
            onOffsetChange={setOffset}
          />
        </>
      )}

      <QuizFormModal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        quiz={editing}
      />

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete quiz"
        message={`Delete ${deleting?.quiz_name}? This fails if scores have already been recorded for it.`}
        pending={deleteMutation.isPending}
      />
    </div>
  );
}

function QuizFormModal({
  open,
  onClose,
  quiz,
}: {
  open: boolean;
  onClose: () => void;
  quiz?: Quiz;
}) {
  const isEdit = Boolean(quiz);
  const [quizName, setQuizName] = useState(quiz?.quiz_name ?? "");
  const [quizDate, setQuizDate] = useState(quiz?.quiz_date ?? todayIso());
  const [subject, setSubject] = useState(quiz?.subject ?? "");
  const [maxMarks, setMaxMarks] = useState(
    quiz ? String(quiz.max_marks) : "20",
  );
  const [error, setError] = useState("");

  const createMutation = useCreateQuiz();
  const updateMutation = useUpdateQuiz(quiz?.quiz_id ?? -1);
  const pending = createMutation.isPending || updateMutation.isPending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!quizName.trim()) {
      setError("Quiz name is required.");
      return;
    }
    if (!maxMarks || Number(maxMarks) <= 0) {
      setError("Max marks must be greater than 0.");
      return;
    }
    try {
      if (isEdit && quiz) {
        await updateMutation.mutateAsync({
          quiz_name: quizName,
          quiz_date: quizDate || null,
          subject: subject || null,
          max_marks: Number(maxMarks),
        });
        toast.success(`Saved ${quizName}`);
      } else {
        await createMutation.mutateAsync({
          quiz_name: quizName,
          quiz_date: quizDate || null,
          subject: subject || null,
          max_marks: Number(maxMarks),
        });
        toast.success(`Added ${quizName}`);
      }
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? "Edit quiz" : "Add quiz"}
      width="md"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Quiz name" required>
          <Input
            value={quizName}
            onChange={(e) => setQuizName(e.target.value)}
            placeholder="e.g. Weekly Current Affairs Quiz"
          />
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Subject">
            <Input
              value={subject ?? ""}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="e.g. Current Affairs"
            />
          </Field>
          <Field label="Quiz date">
            <Input
              type="date"
              value={quizDate ?? ""}
              onChange={(e) => setQuizDate(e.target.value)}
            />
          </Field>
        </div>
        <Field label="Max marks" required>
          <Input
            type="number"
            min="1"
            step="0.5"
            value={maxMarks}
            onChange={(e) => setMaxMarks(e.target.value)}
          />
        </Field>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={pending}>
            {pending ? "Saving…" : isEdit ? "Save changes" : "Add quiz"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
