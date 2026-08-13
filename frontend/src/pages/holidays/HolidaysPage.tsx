import { useState } from "react";
import toast from "react-hot-toast";
import { Plus, Pencil, Trash2, CalendarOff } from "lucide-react";
import {
  PageHeader,
  ErrorBanner,
  EmptyState,
  TableSkeletonRows,
} from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Input, Textarea, Field } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Modal } from "../../components/ui/Modal";
import {
  useHolidays,
  useCreateHoliday,
  useUpdateHoliday,
  useDeleteHoliday,
} from "../../api/holidays";
import { extractErrorMessage } from "../../api/client";
import { formatDate, todayIso } from "../../lib/format";
import { isLibraryHoliday, HOLIDAY_RULE_DESCRIPTION } from "../../lib/holidays";
import type { Holiday } from "../../api/types";

export function HolidaysPage() {
  const { data, isLoading, isError, error } = useHolidays();
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Holiday | undefined>(undefined);
  const [deleting, setDeleting] = useState<Holiday | undefined>(undefined);
  const [deleteError, setDeleteError] = useState("");
  const deleteMutation = useDeleteHoliday();

  async function handleDelete() {
    if (!deleting) return;
    setDeleteError("");
    try {
      await deleteMutation.mutateAsync(deleting.holiday_id);
      toast.success(`Removed ${deleting.name}`);
      setDeleting(undefined);
    } catch (err) {
      setDeleteError(extractErrorMessage(err));
      toast.error(extractErrorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Library"
        title="Holidays"
        description="Additional days the library is closed, on top of the standing rule — attendance rate, streaks, and the calendar treat these as closed."
        action={
          <Button
            variant="primary"
            onClick={() => {
              setEditing(undefined);
              setFormOpen(true);
            }}
          >
            <Plus size={16} /> Add holiday
          </Button>
        }
      />

      <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-border bg-card p-4 text-sm text-slate">
        <CalendarOff size={18} className="mt-0.5 shrink-0 text-slate-light" />
        <p>
          The standing rule — {HOLIDAY_RULE_DESCRIPTION} — is applied
          automatically to every student's analytics. Add a one-off day here
          (a festival, a power cut) for the rare closure that doesn't fit the
          rule; it won't count against anyone's attendance.
        </p>
      </div>

      {isLoading && (
        <Table>
          <Thead>
            <Th>Date</Th>
            <Th>Name</Th>
            <Th>Notes</Th>
            <Th className="text-right">Actions</Th>
          </Thead>
          <TableSkeletonRows rows={5} columns={4} />
        </Table>
      )}
      {isError && <ErrorBanner message={extractErrorMessage(error)} />}
      {data && data.length === 0 && (
        <EmptyState
          title="No holidays recorded"
          description="Everything shown here stays empty until you add a closure day that falls outside the standing rule."
          action={
            <Button
              variant="primary"
              onClick={() => {
                setEditing(undefined);
                setFormOpen(true);
              }}
            >
              <Plus size={16} /> Add holiday
            </Button>
          }
        />
      )}

      {data && data.length > 0 && (
        <Table>
          <Thead>
            <Th>Date</Th>
            <Th>Name</Th>
            <Th>Notes</Th>
            <Th className="text-right">Actions</Th>
          </Thead>
          <tbody>
            {data.map((h) => (
              <Tr key={h.holiday_id}>
                <Td className="font-medium">{formatDate(h.holiday_date)}</Td>
                <Td>{h.name}</Td>
                <Td className="text-slate">{h.notes ?? "—"}</Td>
                <Td>
                  <div className="flex justify-end gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label="Edit holiday"
                      onClick={() => {
                        setEditing(h);
                        setFormOpen(true);
                      }}
                    >
                      <Pencil size={14} />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label="Delete holiday"
                      onClick={() => {
                        setDeleteError("");
                        setDeleting(h);
                      }}
                    >
                      <Trash2 size={14} className="text-rust" />
                    </Button>
                  </div>
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}

      <HolidayFormModal
        key={editing?.holiday_id ?? "new"}
        open={formOpen}
        onClose={() => setFormOpen(false)}
        holiday={editing}
      />

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => {
          setDeleting(undefined);
          setDeleteError("");
        }}
        onConfirm={handleDelete}
        title="Remove holiday"
        message={`Remove ${deleting?.name}? Its date will count as an open day again in attendance analytics.`}
        pending={deleteMutation.isPending}
        error={deleteError || undefined}
      />
    </div>
  );
}

function HolidayFormModal({
  open,
  onClose,
  holiday,
}: {
  open: boolean;
  onClose: () => void;
  holiday?: Holiday;
}) {
  const isEdit = Boolean(holiday);
  const [holidayDate, setHolidayDate] = useState(
    holiday?.holiday_date ?? todayIso(),
  );
  const [name, setName] = useState(holiday?.name ?? "");
  const [notes, setNotes] = useState(holiday?.notes ?? "");
  const [error, setError] = useState("");

  const createMutation = useCreateHoliday();
  const updateMutation = useUpdateHoliday(holiday?.holiday_id ?? 0);
  const pending = createMutation.isPending || updateMutation.isPending;

  // Warn when staff pick a date the standing rule already closes, so they
  // know the entry is informational rather than a change to anyone's stats.
  const alreadyCovered = holidayDate
    ? isLibraryHoliday(holidayDate)
    : false;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!holidayDate) {
      setError("Date is required.");
      return;
    }
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    const payload = {
      holiday_date: holidayDate,
      name: name.trim(),
      notes: notes.trim() || null,
    };
    try {
      if (isEdit && holiday) {
        await updateMutation.mutateAsync(payload);
        toast.success(`Saved ${payload.name}`);
      } else {
        await createMutation.mutateAsync(payload);
        toast.success(`Added ${payload.name}`);
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
      title={isEdit ? "Edit holiday" : "Add holiday"}
      subtitle="A day the library is closed, e.g. a festival or a power cut."
      width="md"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Date" required>
            <Input
              type="date"
              value={holidayDate}
              onChange={(e) => setHolidayDate(e.target.value)}
            />
          </Field>
          <Field label="Name" required>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Pongal, Deepavali"
            />
          </Field>
        </div>
        {alreadyCovered && (
          <div className="rounded-md border border-border bg-paper-dim px-3 py-2 text-xs text-slate">
            This date is already closed by the standing rule (
            {HOLIDAY_RULE_DESCRIPTION}), so it won't change any attendance
            figures — add it only if you want it listed here.
          </div>
        )}
        <Field label="Notes">
          <Textarea
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Optional — e.g. reason for the closure"
          />
        </Field>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={pending}>
            {pending ? "Saving…" : isEdit ? "Save changes" : "Add holiday"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
