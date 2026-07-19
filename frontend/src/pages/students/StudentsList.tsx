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
import { Input, Select } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { IdTab, StatusTab, studentStatusTone } from "../../components/ui/Tabs";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { useStudentSearch, useDeleteStudent } from "../../api/students";
import { extractErrorMessage } from "../../api/client";
import { formatDate } from "../../lib/format";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { StudentFormModal } from "./StudentFormModal";
import type { Student } from "../../api/types";

const LIMIT = 20;

export function StudentsList() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Student | undefined>(undefined);
  const [deleting, setDeleting] = useState<Student | undefined>(undefined);

  const debouncedSearch = useDebouncedValue(search);
  const { data, isLoading, isError, error } = useStudentSearch({
    search: debouncedSearch || undefined,
    status: status || undefined,
    limit: LIMIT,
    offset,
  });
  const deleteMutation = useDeleteStudent();

  async function handleDelete() {
    if (!deleting) return;
    try {
      await deleteMutation.mutateAsync(deleting.student_id);
      toast.success(`Removed ${deleting.name}`);
      setDeleting(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Front desk"
        title="Students"
        description="Every enrolled student's record card — search, add, and update details here."
        action={
          <Button
            variant="primary"
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            <Plus size={16} /> Add student
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap gap-3">
        <div className="relative flex-1 min-w-[220px]">
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
            placeholder="Search by name or student ID…"
            className="pl-9"
          />
        </div>
        <Select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
          className="w-40"
        >
          <option value="">All statuses</option>
          <option value="Active">Active</option>
          <option value="Inactive">Inactive</option>
        </Select>
      </div>

      {isLoading && <Spinner label="Loading students…" />}
      {isError && <ErrorBanner message={extractErrorMessage(error)} />}

      {data && data.length === 0 && (
        <EmptyState
          title="No students found"
          description="Try a different search, or add the first student record."
          action={
            <Button
              variant="primary"
              onClick={() => {
                setEditing(undefined);
                setFormOpen(true);
              }}
            >
              <Plus size={16} /> Add student
            </Button>
          }
        />
      )}

      {data && data.length > 0 && (
        <>
          <Table>
            <Thead>
              <Th>Student</Th>
              <Th>Contact</Th>
              <Th>Joined</Th>
              <Th>Status</Th>
              <Th className="text-right">Actions</Th>
            </Thead>
            <tbody>
              {data.map((s) => (
                <Tr
                  key={s.student_id}
                  onClick={() => navigate(`/students/${s.student_id}`)}
                >
                  <Td>
                    <div className="flex items-center gap-2.5">
                      <IdTab>{s.student_id}</IdTab>
                      <span className="font-medium">{s.name}</span>
                    </div>
                  </Td>
                  <Td className="text-slate">{s.phone || s.email || "—"}</Td>
                  <Td className="text-slate">{formatDate(s.join_date)}</Td>
                  <Td>
                    <StatusTab tone={studentStatusTone(s.status)}>
                      {s.status}
                    </StatusTab>
                  </Td>
                  <Td>
                    <div
                      className="flex justify-end gap-1"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditing(s);
                          setFormOpen(true);
                        }}
                      >
                        <Pencil size={14} />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeleting(s)}
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

      <StudentFormModal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        student={editing}
      />

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete student"
        message={`Delete ${deleting?.name}? This only works if the student has no attendance, library, exam, or quiz history — otherwise set status to Inactive instead.`}
        pending={deleteMutation.isPending}
      />
    </div>
  );
}
