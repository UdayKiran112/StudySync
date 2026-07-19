import { useState } from "react";
import toast from "react-hot-toast";
import { Trash2 } from "lucide-react";
import { PageHeader, Spinner, ErrorBanner, EmptyState, Pagination } from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Field, Input, Select } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { StudentPicker } from "../../components/ui/StudentPicker";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { useOfflineLibraryList, useCreateOfflineUsage, useDeleteOfflineUsage } from "../../api/offlineLibrary";
import { useBooks } from "../../api/books";
import { extractErrorMessage } from "../../api/client";
import { formatDate, todayIso } from "../../lib/format";
import type { Student, OfflineLibraryUsage } from "../../api/types";

const LIMIT = 20;

export function OfflineLibraryPage() {
  const [student, setStudent] = useState<Student | null>(null);
  const [bookId, setBookId] = useState("");
  const [date, setDate] = useState(todayIso());

  const [filterDate, setFilterDate] = useState("");
  const [offset, setOffset] = useState(0);
  const [deleting, setDeleting] = useState<OfflineLibraryUsage | undefined>(undefined);

  const createMutation = useCreateOfflineUsage();
  const deleteMutation = useDeleteOfflineUsage();
  const { data: books } = useBooks({ limit: 200 });

  const { data, isLoading, isError, error } = useOfflineLibraryList({
    date_: filterDate || undefined,
    limit: LIMIT,
    offset,
  });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!student) {
      toast.error("Pick a student first");
      return;
    }
    try {
      await createMutation.mutateAsync({
        student_id: student.student_id,
        book_id: bookId || null,
        date,
      });
      toast.success(`Logged offline library visit for ${student.name}`);
      setStudent(null);
      setBookId("");
      setDate(todayIso());
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  async function handleDelete() {
    if (!deleting) return;
    try {
      await deleteMutation.mutateAsync(deleting.usage_id);
      toast.success("Visit removed");
      setDeleting(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Library"
        title="Offline library"
        description="Log a student reading a catalogued book or their own material on-site."
      />

      <div className="mb-8 rounded-lg border border-border bg-card p-6">
        <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-4 sm:grid-cols-[2fr_1.5fr_1fr_auto] sm:items-end">
          <Field label="Student" required>
            <StudentPicker value={student} onChange={setStudent} />
          </Field>
          <Field label="Book" hint="Leave blank for own material">
            <Select value={bookId} onChange={(e) => setBookId(e.target.value)}>
              <option value="">Own material</option>
              {books?.map((b) => (
                <option key={b.book_id} value={b.book_id}>
                  {b.book_id} — {b.title}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Date" required>
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Button type="submit" variant="primary" disabled={createMutation.isPending}>
            {createMutation.isPending ? "Saving…" : "Log visit"}
          </Button>
        </form>
      </div>

      <div className="mb-4 flex flex-wrap gap-3">
        <Input
          type="date"
          value={filterDate}
          onChange={(e) => {
            setFilterDate(e.target.value);
            setOffset(0);
          }}
          className="w-44"
        />
      </div>

      {isLoading && <Spinner label="Loading visits…" />}
      {isError && <ErrorBanner message={extractErrorMessage(error)} />}
      {data && data.length === 0 && <EmptyState title="No offline library visits match these filters" />}

      {data && data.length > 0 && (
        <>
          <Table>
            <Thead>
              <Th>Student</Th>
              <Th>Date</Th>
              <Th>Book</Th>
              <Th className="text-right">Actions</Th>
            </Thead>
            <tbody>
              {data.map((u) => (
                <Tr key={u.usage_id}>
                  <Td className="font-mono text-xs">{u.student_id}</Td>
                  <Td>{formatDate(u.date)}</Td>
                  <Td>{u.book_id ?? <span className="text-slate">Own material</span>}</Td>
                  <Td className="text-right">
                    <Button size="sm" variant="ghost" onClick={() => setDeleting(u)}>
                      <Trash2 size={14} className="text-rust" />
                    </Button>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
          <Pagination offset={offset} limit={LIMIT} count={data.length} onOffsetChange={setOffset} />
        </>
      )}

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete visit"
        message="This removes the offline library visit permanently."
        pending={deleteMutation.isPending}
      />
    </div>
  );
}
