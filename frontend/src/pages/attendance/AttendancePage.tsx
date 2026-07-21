import { useState } from "react";
import toast from "react-hot-toast";
import { Trash2, Pencil, LogIn, LogOut, X } from "lucide-react";
import {
  PageHeader,
  Spinner,
  ErrorBanner,
  EmptyState,
  Pagination,
} from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Field, Input, Select } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import { StudentPicker } from "../../components/ui/StudentPicker";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Modal } from "../../components/ui/Modal";
import { StatusTab } from "../../components/ui/Tabs";
import {
  useAttendanceList,
  useCheckIn,
  useCheckOut,
  useUpdateAttendance,
  useDeleteAttendance,
} from "../../api/attendance";
import { extractErrorMessage } from "../../api/client";
import {
  formatDate,
  formatDuration,
  todayIso,
  nowHHMM,
} from "../../lib/format";
import type { Student, Attendance } from "../../api/types";

const LIMIT = 20;

function sessionTone(session: string): "forest" | "brass" | "slate" {
  if (session === "Full Day") return "forest";
  if (session === "Morning") return "brass";
  return "slate";
}

export function AttendancePage() {
  const [mode, setMode] = useState<"check-in" | "check-out">("check-in");
  const [student, setStudent] = useState<Student | null>(null);
  const [entryDate, setEntryDate] = useState(todayIso());
  const [entryTime, setEntryTime] = useState(nowHHMM());
  const [filterDate, setFilterDate] = useState("");
  const [filterSession, setFilterSession] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<Attendance | undefined>(undefined);
  const [deleting, setDeleting] = useState<Attendance | undefined>(undefined);

  const checkIn = useCheckIn();
  const checkOut = useCheckOut();
  const deleteMutation = useDeleteAttendance();

  // Backend takes a date range (date_from/date_to), not a single date, and
  // no longer paginates this endpoint — it always returns everything
  // matching the filter. A single-day filter is just date_from === date_to;
  // paging happens client-side below with the full result set in hand.
  const {
    data: allData,
    isLoading,
    isError,
    error,
  } = useAttendanceList({
    date_from: filterDate || undefined,
    date_to: filterDate || undefined,
    session: filterSession || undefined,
  });
  const data = allData?.slice(offset, offset + LIMIT);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!student) {
      toast.error("Pick a student first");
      return;
    }
    if (!entryTime) {
      toast.error("Enter a time");
      return;
    }
    try {
      if (mode === "check-in") {
        // No session field — the backend derives it from check_in time
        // (and may reclassify it to "Full Day" at check-out).
        await checkIn.mutateAsync({
          student_id: student.student_id,
          date: entryDate || undefined,
          check_in: entryTime,
        });
        toast.success(`Checked in ${student.name} at ${entryTime}`);
      } else {
        // No session/date needed either — the backend finds this
        // student's one currently-open session automatically.
        await checkOut.mutateAsync({
          student_id: student.student_id,
          check_out: entryTime,
        });
        toast.success(`Checked out ${student.name} at ${entryTime}`);
      }
      setStudent(null);
      setEntryDate(todayIso());
      setEntryTime(nowHHMM());
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  async function handleDelete() {
    if (!deleting) return;
    try {
      await deleteMutation.mutateAsync(deleting.attendance_id);
      toast.success("Attendance record removed");
      setDeleting(undefined);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  function filterByDate(date: string) {
    setFilterDate(date);
    setOffset(0);
  }

  const pending = checkIn.isPending || checkOut.isPending;

  return (
    <div>
      <PageHeader
        eyebrow="Front desk"
        title="Attendance"
        description="Log arrivals and departures. Session (Morning / Afternoon / Full Day) is detected automatically from the times entered."
      />

      <div className="mb-8 rounded-lg border border-border bg-card p-6">
        <div className="mb-4 flex gap-1 rounded-md bg-paper-dim p-1 w-fit">
          <button
            onClick={() => setMode("check-in")}
            className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition-colors ${
              mode === "check-in" ? "bg-card text-ink shadow-sm" : "text-slate"
            }`}
          >
            <LogIn size={14} /> Check in
          </button>
          <button
            onClick={() => setMode("check-out")}
            className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition-colors ${
              mode === "check-out" ? "bg-card text-ink shadow-sm" : "text-slate"
            }`}
          >
            <LogOut size={14} /> Check out
          </button>
        </div>

        <form
          onSubmit={handleSubmit}
          className={
            mode === "check-in"
              ? "grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-[2fr_1fr_1fr_auto] lg:items-end"
              : "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_auto] lg:items-end"
          }
        >
          <Field label="Student" required>
            <StudentPicker value={student} onChange={setStudent} />
          </Field>
          {mode === "check-in" && (
            <Field label="Date" required>
              <Input
                type="date"
                value={entryDate}
                onChange={(e) => setEntryDate(e.target.value)}
              />
            </Field>
          )}
          <Field
            label={mode === "check-in" ? "Check-in time" : "Check-out time"}
            required
          >
            <Input
              type="time"
              value={entryTime}
              onChange={(e) => setEntryTime(e.target.value)}
            />
          </Field>
          <Button type="submit" variant="primary" disabled={pending}>
            {pending
              ? "Saving…"
              : mode === "check-in"
                ? "Check in"
                : "Check out"}
          </Button>
        </form>
        {mode === "check-out" && (
          <p className="mt-3 text-xs text-slate-light">
            Checking out finds this student's one open session automatically —
            no need to pick a date or session.
          </p>
        )}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Input
          type="date"
          value={filterDate}
          onChange={(e) => filterByDate(e.target.value)}
          className="w-44"
        />
        <Select
          value={filterSession}
          onChange={(e) => {
            setFilterSession(e.target.value);
            setOffset(0);
          }}
          className="w-40"
        >
          <option value="">All sessions</option>
          <option value="Morning">Morning</option>
          <option value="Afternoon">Afternoon</option>
          <option value="Full Day">Full Day</option>
        </Select>
        {filterDate && (
          <button
            onClick={() => filterByDate("")}
            className="flex items-center gap-1 rounded-full bg-brass/15 px-3 py-1 text-xs font-medium text-brass hover:bg-brass/25"
          >
            {formatDate(filterDate)} <X size={12} />
          </button>
        )}
      </div>

      {isLoading && <Spinner label="Loading attendance…" />}
      {isError && <ErrorBanner message={extractErrorMessage(error)} />}
      {data && data.length === 0 && (
        <EmptyState title="No attendance records match these filters" />
      )}

      {data && data.length > 0 && (
        <>
          <Table>
            <Thead>
              <Th>Student</Th>
              <Th>Date</Th>
              <Th>Session</Th>
              <Th>Check-in</Th>
              <Th>Check-out</Th>
              <Th>Duration</Th>
              <Th className="text-right">Actions</Th>
            </Thead>
            <tbody>
              {data.map((a) => (
                <Tr key={a.attendance_id}>
                  <Td className="font-mono text-xs">{a.student_id}</Td>
                  <Td
                    onClick={() => filterByDate(a.date)}
                    className="cursor-pointer hover:text-brass hover:underline"
                    title="Show only this date"
                  >
                    {formatDate(a.date)}
                  </Td>
                  <Td>
                    <StatusTab tone={sessionTone(a.session)}>
                      {a.session}
                    </StatusTab>
                  </Td>
                  <Td className="font-mono text-xs">{a.check_in ?? "—"}</Td>
                  <Td className="font-mono text-xs">
                    {a.check_out ?? (
                      <span className="text-brass">Still in</span>
                    )}
                  </Td>
                  <Td className="text-slate">
                    {formatDuration(a.duration_minutes)}
                  </Td>
                  <Td className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setEditing(a)}
                      >
                        <Pencil size={14} />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeleting(a)}
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
            total={allData?.length}
            onOffsetChange={setOffset}
          />
        </>
      )}

      {editing && (
        <EditAttendanceModal
          open={Boolean(editing)}
          onClose={() => setEditing(undefined)}
          record={editing}
        />
      )}

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete attendance record"
        message="This removes the record permanently. Use Edit instead if you just need to fix a typo'd time."
        pending={deleteMutation.isPending}
      />
    </div>
  );
}

function EditAttendanceModal({
  open,
  onClose,
  record,
}: {
  open: boolean;
  onClose: () => void;
  record: Attendance;
}) {
  const [checkIn, setCheckIn] = useState(record.check_in ?? "");
  const [checkOut, setCheckOut] = useState(record.check_out ?? "");
  const [error, setError] = useState("");
  const updateMutation = useUpdateAttendance(record.attendance_id);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await updateMutation.mutateAsync({
        check_in: checkIn || null,
        check_out: checkOut || null,
      });
      toast.success("Attendance record updated");
      onClose();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Edit attendance"
      subtitle={`Student ${record.student_id} · ${formatDate(record.date)} — session and duration recalculate automatically`}
      width="sm"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Check-in time">
          <Input
            type="time"
            value={checkIn}
            onChange={(e) => setCheckIn(e.target.value)}
          />
        </Field>
        <Field label="Check-out time">
          <Input
            type="time"
            value={checkOut}
            onChange={(e) => setCheckOut(e.target.value)}
          />
        </Field>
        {error && <p className="text-sm text-rust">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={updateMutation.isPending}
          >
            {updateMutation.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
