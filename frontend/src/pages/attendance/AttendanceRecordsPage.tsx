import { useState } from "react";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { Trash2, Pencil } from "lucide-react";
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
import { StatusTab, sessionTone } from "../../components/ui/Tabs";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Modal } from "../../components/ui/Modal";
import { StudentPicker } from "../../components/ui/StudentPicker";
import {
  useAttendanceList,
  useUpdateAttendance,
  useDeleteAttendance,
} from "../../api/attendance";
import { extractErrorMessage } from "../../api/client";
import { useStudent } from "../../api/students";
import { formatDate, formatDuration, formatClockHM, todayIso } from "../../lib/format";
import { LiveClock, OpenSessionDuration } from "../../components/ui/LiveClock";
import { StudentName } from "../../components/ui/StudentName";
import { ExportMenu } from "../../components/ui/ExportMenu";
import type { ExportColumn, ExportRow } from "../../components/ui/ExportMenu";
import { RecordToolbar } from "../../components/ui/RecordToolbar";
import type { Attendance, Student } from "../../api/types";

const LIMIT = 20;

function daysAgoIso(days: number) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

const EXPORT_COLUMNS: ExportColumn[] = [
  { key: "student_id", label: "Student ID" },
  { key: "date", label: "Date" },
  { key: "session", label: "Session" },
  { key: "check_in", label: "Check-in" },
  { key: "check_out", label: "Check-out" },
  { key: "duration_minutes", label: "Duration (min)" },
];

function toExportRow(record: Attendance): ExportRow {
  return {
    student_id: record.student_id,
    date: record.date,
    session: record.session,
    check_in: record.check_in ?? "",
    check_out: record.check_out ?? "",
    duration_minutes: record.duration_minutes ?? "",
  };
}

export function AttendanceRecordsPage() {
  const [dateFrom, setDateFrom] = useState(daysAgoIso(30));
  const [dateTo, setDateTo] = useState(todayIso());
  const [session, setSession] = useState("");
  const [studentFilter, setStudentFilter] = useState<Student | null>(null);
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<Attendance | undefined>(undefined);
  const [deleting, setDeleting] = useState<Attendance | undefined>(undefined);
  const [deleteError, setDeleteError] = useState("");

  const {
    data: page,
    isLoading,
    isError,
    error,
  } = useAttendanceList({
    student_id: studentFilter?.student_id,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    session: session || undefined,
    limit: LIMIT,
    offset,
  });

  const data = page?.items;
  const total = page?.total ?? 0;
  const deleteMutation = useDeleteAttendance();

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

  function resetFilters() {
    setDateFrom("");
    setDateTo("");
    setSession("");
    setStudentFilter(null);
    setOffset(0);
  }

  return (
    <div>
      <PageHeader
        eyebrow="Front desk"
        title="Attendance records"
        description="Search historical attendance by date range or session. Edit or remove records from the same list."
        action={
          <div className="rounded-2xl border border-border bg-card px-4 py-3 text-right text-slate">
            <LiveClock size="sm" />
            <Link
              to="/attendance"
              className="mt-4 inline-flex items-center rounded-full bg-ink px-3 py-1.5 text-xs font-semibold text-paper shadow-sm transition hover:bg-ink-light"
            >
              Back to today's attendance
            </Link>
          </div>
        }
      />

      <RecordToolbar
        title="Search historic attendance"
        description={
          total === 0
            ? "Use the filters to narrow the record set."
            : `Found ${total} matching record${total === 1 ? "" : "s"}.`
        }
        controls={
          <>
            <Field label="Student">
              <StudentPicker
                value={studentFilter}
                onChange={(s) => {
                  setStudentFilter(s);
                  setOffset(0);
                }}
                activeOnly={false}
              />
            </Field>
            <Field label="From">
              <Input
                type="date"
                value={dateFrom}
                onChange={(e) => {
                  setDateFrom(e.target.value);
                  setOffset(0);
                }}
              />
            </Field>
            <Field label="To">
              <Input
                type="date"
                value={dateTo}
                onChange={(e) => {
                  setDateTo(e.target.value);
                  setOffset(0);
                }}
              />
            </Field>
            <Field label="Session">
              <Select
                value={session}
                onChange={(e) => {
                  setSession(e.target.value);
                  setOffset(0);
                }}
              >
                <option value="">All sessions</option>
                <option value="Morning">Morning</option>
                <option value="Afternoon">Afternoon</option>
                <option value="Full Day">Full Day</option>
              </Select>
            </Field>
          </>
        }
        actions={
          <>
            <ExportMenu
              title="Attendance records"
              filename={`attendance-records-${todayIso()}`}
              columns={EXPORT_COLUMNS}
              getRows={() => (data ?? []).map(toExportRow)}
            />
            <Button variant="secondary" size="sm" onClick={resetFilters}>
              Clear filters
            </Button>
          </>
        }
      />

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
              {data.map((record) => {
                              const checkInDate = record.check_in ? new Date(`${record.date}T${record.check_in}`) : null;
                              const checkOutDate = record.check_out ? new Date(`${record.date}T${record.check_out}`) : null;
                              const open = Boolean(checkInDate && !checkOutDate);
                              const checkInDisplay = checkInDate ? formatClockHM(checkInDate) : "—";
                              const checkOutDisplay = checkOutDate ? formatClockHM(checkOutDate) : "--";
                              const closedDuration =
                                checkInDate && checkOutDate
                                  ? formatDuration(Math.max(0, Math.round((checkOutDate.getTime() - checkInDate.getTime()) / 60000)))
                                  : formatDuration(record.duration_minutes ?? 0);

                              return (
                                <Tr key={record.attendance_id}>
                                  <Td>
                                    <StudentName studentId={record.student_id} />
                                  </Td>
                                  <Td>{formatDate(record.date)}</Td>
                                  <Td>
                                    <StatusTab tone={sessionTone(record.session)}>
                                      {record.session}
                                    </StatusTab>
                                  </Td>
                                  <Td className="font-mono text-xs">{checkInDisplay}</Td>
                                  <Td className="font-mono text-xs">{checkOutDisplay}</Td>
                                  <Td className="text-slate">
                                    {open && checkInDate ? <OpenSessionDuration checkInDate={checkInDate} /> : closedDuration}
                                  </Td>
                                  <Td className="text-right">
                                    <div className="flex justify-end gap-1">
                                      <Button
                                        size="sm"
                                        variant="ghost"
                                        aria-label="Edit attendance record"
                                        onClick={() => setEditing(record)}
                                      >
                                        <Pencil size={14} />
                                      </Button>
                                      <Button
                                        size="sm"
                                        variant="ghost"
                                        aria-label="Delete attendance record"
                                        onClick={() => {
                                          setDeleteError("");
                                          setDeleting(record);
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
            total={total}
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
  const { data: student } = useStudent(record.student_id);

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
      subtitle={`Student ${student?.name ?? record.student_id} (${record.student_id}) · ${formatDate(record.date)} — session and duration recalculate automatically`}
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
