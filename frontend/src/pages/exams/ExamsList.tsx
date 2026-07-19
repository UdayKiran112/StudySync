import { useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { Plus, Search, Pencil, Trash2 } from "lucide-react";
import { PageHeader, Spinner, ErrorBanner, EmptyState, Pagination } from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Input, Field } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Modal } from "../../components/ui/Modal";
import { useExams, useCreateExam, useUpdateExam, useDeleteExam } from "../../api/exams";
import { extractErrorMessage } from "../../api/client";
import { formatDate, todayIso } from "../../lib/format";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import type { Exam } from "../../api/types";

const LIMIT = 20;

export function ExamsList() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Exam | undefined>(undefined);
  const [deleting, setDeleting] = useState<Exam | undefined>(undefined);

  const debouncedSearch = useDebouncedValue(search);
  const { data, isLoading, isError, error } = useExams({ search: debouncedSearch || undefined, limit: LIMIT, offset });
  const deleteMutation = useDeleteExam();

  async function handleDelete() {
    if (!deleting) return;
    try {
      await deleteMutation.mutateAsync(deleting.exam_id);
      toast.success(`Removed ${deleting.exam_name}`);
      setDeleting(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Assessments"
        title="Exams"
        description="Manage the exam catalog. Open an exam to record student marks."
        action={
          <Button
            variant="primary"
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            <Plus size={16} /> Add exam
          </Button>
        }
      />

      <div className="relative mb-4 max-w-sm">
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-light" />
        <Input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
          placeholder="Search by exam name…"
          className="pl-9"
        />
      </div>

      {isLoading && <Spinner label="Loading exams…" />}
      {isError && <ErrorBanner message={extractErrorMessage(error)} />}
      {data && data.length === 0 && <EmptyState title="No exams found" />}

      {data && data.length > 0 && (
        <>
          <Table>
            <Thead>
              <Th>Exam</Th>
              <Th>Subject</Th>
              <Th>Date</Th>
              <Th>Max marks</Th>
              <Th className="text-right">Actions</Th>
            </Thead>
            <tbody>
              {data.map((ex) => (
                <Tr key={ex.exam_id} onClick={() => navigate(`/exams/${ex.exam_id}`)}>
                  <Td className="font-medium">{ex.exam_name}</Td>
                  <Td className="text-slate">{ex.subject ?? "—"}</Td>
                  <Td className="text-slate">{formatDate(ex.exam_date)}</Td>
                  <Td className="font-mono text-xs">{ex.max_marks}</Td>
                  <Td>
                    <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditing(ex);
                          setFormOpen(true);
                        }}
                      >
                        <Pencil size={14} />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setDeleting(ex)}>
                        <Trash2 size={14} className="text-rust" />
                      </Button>
                    </div>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
          <Pagination offset={offset} limit={LIMIT} count={data.length} onOffsetChange={setOffset} />
        </>
      )}

      <ExamFormModal open={formOpen} onClose={() => setFormOpen(false)} exam={editing} />

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete exam"
        message={`Delete ${deleting?.exam_name}? This fails if marks have already been recorded for it.`}
        pending={deleteMutation.isPending}
      />
    </div>
  );
}

function ExamFormModal({ open, onClose, exam }: { open: boolean; onClose: () => void; exam?: Exam }) {
  const isEdit = Boolean(exam);
  const [examName, setExamName] = useState(exam?.exam_name ?? "");
  const [examDate, setExamDate] = useState(exam?.exam_date ?? todayIso());
  const [subject, setSubject] = useState(exam?.subject ?? "");
  const [maxMarks, setMaxMarks] = useState(exam ? String(exam.max_marks) : "100");
  const [error, setError] = useState("");

  const createMutation = useCreateExam();
  const updateMutation = useUpdateExam(exam?.exam_id ?? -1);
  const pending = createMutation.isPending || updateMutation.isPending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!examName.trim()) {
      setError("Exam name is required.");
      return;
    }
    if (!maxMarks || Number(maxMarks) <= 0) {
      setError("Max marks must be greater than 0.");
      return;
    }
    try {
      if (isEdit && exam) {
        await updateMutation.mutateAsync({
          exam_name: examName,
          exam_date: examDate || null,
          subject: subject || null,
          max_marks: Number(maxMarks),
        });
        toast.success(`Saved ${examName}`);
      } else {
        await createMutation.mutateAsync({
          exam_name: examName,
          exam_date: examDate || null,
          subject: subject || null,
          max_marks: Number(maxMarks),
        });
        toast.success(`Added ${examName}`);
      }
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={isEdit ? "Edit exam" : "Add exam"} width="md">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Exam name" required>
          <Input value={examName} onChange={(e) => setExamName(e.target.value)} placeholder="e.g. Prelims Mock Test 3" />
        </Field>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Subject">
            <Input value={subject ?? ""} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. General Studies" />
          </Field>
          <Field label="Exam date">
            <Input type="date" value={examDate ?? ""} onChange={(e) => setExamDate(e.target.value)} />
          </Field>
        </div>
        <Field label="Max marks" required>
          <Input type="number" min="1" step="0.5" value={maxMarks} onChange={(e) => setMaxMarks(e.target.value)} />
        </Field>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={pending}>
            {pending ? "Saving…" : isEdit ? "Save changes" : "Add exam"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export { ExamFormModal };
