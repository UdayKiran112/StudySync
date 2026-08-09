import { useState, useEffect, useMemo } from "react";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { Trash2, Pencil, LogIn, LogOut, RefreshCw, X } from "lucide-react";
import {
  PageHeader,
  ErrorBanner,
  EmptyState,
  Pagination,
  TableSkeletonRows,
} from "../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { Field, Input, Select } from "../../components/ui/Form";
import { Button } from "../../components/ui/Button";
import {
  LiveClock,
  OpenSessionTime,
  OpenSessionDuration,
} from "../../components/ui/LiveClock";
import { StudentPicker } from "../../components/ui/StudentPicker";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Modal } from "../../components/ui/Modal";
import { StatusTab, sessionTone } from "../../components/ui/Tabs";
import {
  useAttendanceList,
  useCheckIn,
  useCheckOut,
  useUpdateAttendance,
  useDeleteAttendance,
} from "../../api/attendance";
import { extractErrorMessage } from "../../api/client";
import { useZkSync } from "../../api/zkteco";
import {
  formatDate,
  formatDuration,
  formatClockTime,
  todayIso,
  nowHHMM,
} from "../../lib/format";
import type { Student, Attendance } from "../../api/types";

const LIMIT = 20;

export function AttendancePage() {
  const [mode, setMode] = useState<"check-in" | "check-out">("check-in");
  const [student, setStudent] = useState<Student | null>(null);
  const [entryDate, setEntryDate] = useState(todayIso());
  const [entryTime, setEntryTime] = useState(nowHHMM());
  // Tracks whether staff has manually edited the date/time fields. While
  // untouched, they keep following the live clock below; once edited, we
  // stop overwriting what the user typed.
  const [dateTouched, setDateTouched] = useState(false);
  const [timeTouched, setTimeTouched] = useState(false);
  const [filterSession, setFilterSession] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<Attendance | undefined>(undefined);
  const [deleting, setDeleting] = useState<Attendance | undefined>(undefined);
  const [deleteError, setDeleteError] = useState("");

  const today = todayIso();

  const checkIn = useCheckIn();
  const checkOut = useCheckOut();
  const deleteMutation = useDeleteAttendance();
  const zkSync = useZkSync();

  // Backend takes a date range (date_from/date_to) and paginates
  // server-side. Today's sheet is a single day, so a generous limit
  // grabs the whole day in one page; paging matters on the records page.

  // Keep the check-in/out date & time fields following the live clock until
  // the staff member edits one manually. This ticks every second, but the
  // setState calls below bail out (React) when the value is unchanged, so the
  // page only re-renders at each minute boundary. Previously these were only
  // ever set once on mount, so the form silently went stale until a page
  // refresh (or a successful submit, which happened to reset them).
  useEffect(() => {
    if (dateTouched && timeTouched) return;
    const timer = window.setInterval(() => {
      if (!dateTouched) setEntryDate(todayIso());
      if (!timeTouched) setEntryTime(nowHHMM());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [dateTouched, timeTouched]);

  const {
    data: todayPage,
    isLoading,
    isError,
    error,
  } = useAttendanceList({
    date_from: today,
    date_to: today,
    session: filterSession || undefined,
    limit: 500,
  });

  const allData = todayPage?.items;
  const totalToday = todayPage?.total ?? 0;

  const sortedTodayRecords = useMemo(
    () =>
      allData
        ? [...allData].sort(
            (a, b) =>
              (a.check_in ?? "").localeCompare(b.check_in ?? "") ||
              a.attendance_id - b.attendance_id,
          )
        : undefined,
    [allData],
  );

  const data = sortedTodayRecords?.slice(offset, offset + LIMIT);

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
      setDateTouched(false);
      setTimeTouched(false);
    } catch (err) {
      toast.error(extractErrorMessage(err));
    }
  }

  async function handleDelete() {
    if (!deleting) return;
    setDeleteError("");
    try {
      await deleteMutation.mutateAsync(deleting.attendance_id);
      toast.success("Attendance record removed");
      setDeleting(undefined);
    } catch (err) {
      setDeleteError(extractErrorMessage(err));
      toast.error(extractErrorMessage(err));
    }
  }

  async function handleZkSync() {
    try {
      const result = await zkSync.mutateAsync();
      toast.success(
        `Applied ${result.imported} swipe${result.imported === 1 ? "" : "s"} from the device`,
      );
      if (result.unknown_students > 0) {
        toast(
          `${result.unknown_students} swipe${result.unknown_students === 1 ? "" : "s"} didn't match a student and were skipped.`,
          { icon: "⚠️" },
        );
      }
      if (result.renewed > 0) {
        toast(
          `${result.renewed} lapsed membership${result.renewed === 1 ? "" : "s"} auto-renewed on check-in.`,
        );
      }
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
        description="Log arrivals and departures. Today's attendance is shown below; historical records are available on a separate page."
        action={
          <div className="flex flex-col items-end gap-3">
            <LiveClock />
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Link
                to="/attendance/records"
                className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-xs font-medium text-ink shadow-sm transition hover:bg-paper-dim"
              >
                View records
              </Link>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={handleZkSync}
                disabled={zkSync.isPending}
              >
                <RefreshCw
                  size={14}
                  className={zkSync.isPending ? "animate-spin" : ""}
                />
                {zkSync.isPending ? "Syncing…" : "Sync from device"}
              </Button>
            </div>
          </div>
        }
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
            <StudentPicker value={student} onChange={setStudent} autoFocus />
          </Field>
          {mode === "check-in" && (
            <Field label="Date" required>
              <Input
                type="date"
                value={entryDate}
                onChange={(e) => {
                  setEntryDate(e.target.value);
                  setDateTouched(true);
                }}
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
              onChange={(e) => {
                setEntryTime(e.target.value);
                setTimeTouched(true);
              }}
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

      <div className="mb-4 grid gap-4 lg:grid-cols-[1fr_auto]">
        <div className="rounded-2xl border border-border bg-paper p-4 text-sm text-slate">
          <p className="font-medium text-ink">Today's attendance</p>
          <p className="mt-2">
            Showing {totalToday} record{totalToday === 1 ? "" : "s"} for today.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Select
            value={filterSession}
            onChange={(e) => {
              setFilterSession(e.target.value);
              setOffset(0);
            }}
            className="w-44"
          >
            <option value="">All sessions</option>
            <option value="Morning">Morning</option>
            <option value="Afternoon">Afternoon</option>
            <option value="Full Day">Full Day</option>
          </Select>
          {filterSession && (
            <button
              onClick={() => {
                setFilterSession("");
                setOffset(0);
              }}
              className="flex items-center gap-1 rounded-full bg-brass/15 px-3 py-1 text-xs font-medium text-brass hover:bg-brass/25"
            >
              {filterSession} <X size={12} />
            </button>
          )}
        </div>
      </div>

      {isLoading && (
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
          <TableSkeletonRows rows={6} columns={7} />
        </Table>
      )}
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
              {data.map((a) => {
                const checkInDate = a.check_in
                  ? new Date(`${a.date}T${a.check_in}`)
                  : null;
                const checkOutDate = a.check_out
                  ? new Date(`${a.date}T${a.check_out}`)
                  : null;
                const open = Boolean(checkInDate && !checkOutDate);
                const checkInDisplay = checkInDate
                  ? formatClockTime(checkInDate)
                  : "—";
                const checkOutDisplay = checkOutDate
                  ? formatClockTime(checkOutDate)
                  : "—";
                const closedDuration =
                  checkInDate && checkOutDate
                    ? formatDuration(
                        Math.max(
                          0,
                          Math.round(
                            (checkOutDate.getTime() - checkInDate.getTime()) /
                              60000,
                          ),
                        ),
                      )
                    : formatDuration(a.duration_minutes ?? 0);

                return (
                  <Tr key={a.attendance_id}>
                    <Td className="font-mono text-xs">{a.student_id}</Td>
                    <Td>{formatDate(a.date)}</Td>
                    <Td>
                      <StatusTab tone={sessionTone(a.session)}>
                        {a.session}
                      </StatusTab>
                    </Td>
                    <Td className="font-mono text-xs">{checkInDisplay}</Td>
                    <Td className="font-mono text-xs">
                      {open ? (
                        <OpenSessionTime />
                      ) : (
                        checkOutDisplay || "—"
                      )}
                    </Td>
                    <Td className="text-slate">
                      {open && checkInDate ? (
                        <OpenSessionDuration checkInDate={checkInDate} />
                      ) : (
                        closedDuration
                      )}
                    </Td>
                    <Td className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label="Edit attendance record"
                          onClick={() => setEditing(a)}
                        >
                          <Pencil size={14} />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label="Delete attendance record"
                          onClick={() => {
                            setDeleteError("");
                            setDeleting(a);
                          }}
                        >
                          <Trash2 size={14} className="text-rust" />
                        </Button>
                      </div>
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
          <Pagination
            offset={offset}
            limit={LIMIT}
            count={data.length}
            total={totalToday}
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
        onClose={() => {
          setDeleting(undefined);
          setDeleteError("");
        }}
        onConfirm={handleDelete}
        title="Delete attendance record"
        message="This removes the record permanently. Use Edit instead if you just need to fix a typo'd time."
        pending={deleteMutation.isPending}
        error={deleteError || undefined}
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
