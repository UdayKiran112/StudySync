import { useState } from "react";
import toast from "react-hot-toast";
import { Trash2, LogIn, LogOut } from "lucide-react";
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
import {
  useAttendanceList,
  useCheckIn,
  useCheckOut,
  useDeleteAttendance,
} from "../../api/attendance";
import { extractErrorMessage } from "../../api/client";
import {
  formatDate,
  formatDuration,
  todayIso,
  nowHHMM,
} from "../../lib/format";
import type { Student } from "../../api/types";
import type { Attendance } from "../../api/types";

const LIMIT = 20;

export function AttendancePage() {
  const [mode, setMode] = useState<"check-in" | "check-out">("check-in");
  const [student, setStudent] = useState<Student | null>(null);
  const [session, setSession] = useState<"Morning" | "Afternoon">("Morning");
  const [entryDate, setEntryDate] = useState(todayIso());
  const [entryTime, setEntryTime] = useState(nowHHMM());
  const [filterDate, setFilterDate] = useState("");
  const [filterSession, setFilterSession] = useState("");
  const [offset, setOffset] = useState(0);
  const [deleting, setDeleting] = useState<Attendance | undefined>(undefined);

  const checkIn = useCheckIn();
  const checkOut = useCheckOut();
  const deleteMutation = useDeleteAttendance();

  const { data, isLoading, isError, error } = useAttendanceList({
    date_: filterDate || undefined,
    session: filterSession || undefined,
    limit: LIMIT,
    offset,
  });

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
        await checkIn.mutateAsync({
          student_id: student.student_id,
          session,
          date: entryDate || undefined,
          check_in: entryTime,
        });
        toast.success(
          `Checked in ${student.name} (${session}) at ${entryTime}`,
        );
      } else {
        await checkOut.mutateAsync({
          student_id: student.student_id,
          session,
          date: entryDate || undefined,
          check_out: entryTime,
        });
        toast.success(
          `Checked out ${student.name} (${session}) at ${entryTime}`,
        );
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

  const pending = checkIn.isPending || checkOut.isPending;

  return (
    <div>
      <PageHeader
        eyebrow="Front desk"
        title="Attendance"
        description="Log arrivals and departures for the Morning and Afternoon sessions."
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
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_1fr_auto] lg:items-end"
        >
          <Field label="Student" required>
            <StudentPicker value={student} onChange={setStudent} />
          </Field>
          <Field label="Session" required>
            <Select
              value={session}
              onChange={(e) =>
                setSession(e.target.value as "Morning" | "Afternoon")
              }
            >
              <option value="Morning">Morning</option>
              <option value="Afternoon">Afternoon</option>
            </Select>
          </Field>
          <Field label="Date" required>
            <Input
              type="date"
              value={entryDate}
              onChange={(e) => setEntryDate(e.target.value)}
            />
          </Field>
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
        </Select>
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
                  <Td>{formatDate(a.date)}</Td>
                  <Td>{a.session}</Td>
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
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setDeleting(a)}
                    >
                      <Trash2 size={14} className="text-rust" />
                    </Button>
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

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete attendance record"
        message="This removes the record permanently. Use this only to correct a mistaken entry."
        pending={deleteMutation.isPending}
      />
    </div>
  );
}
