import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { Trash2, Pencil } from "lucide-react";
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
import { StatusTab } from "../../components/ui/Tabs";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Modal } from "../../components/ui/Modal";
import {
  useAttendanceList,
  useUpdateAttendance,
  useDeleteAttendance,
} from "../../api/attendance";
import { extractErrorMessage } from "../../api/client";
import { formatDate, formatDuration, formatClockTime } from "../../lib/format";
import { LiveClock, OpenSessionTime, OpenSessionDuration } from "../../components/ui/LiveClock";
import type { Attendance } from "../../api/types";

const LIMIT = 20;

export function AttendanceRecordsPage() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [session, setSession] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<Attendance | undefined>(undefined);
  const [deleting, setDeleting] = useState<Attendance | undefined>(undefined);

  const {
    data: allData,
    isLoading,
    isError,
    error,
  } = useAttendanceList({
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    session: session || undefined,
  });

  const sortedData = useMemo(
    () =>
      allData
        ? [...allData].sort(
            (a, b) =>
              b.date.localeCompare(a.date) ||
              (a.check_in ?? "").localeCompare(b.check_in ?? "") ||
              a.attendance_id - b.attendance_id,
          )
        : undefined,
    [allData],
  );

  const data = sortedData?.slice(offset, offset + LIMIT);
  const total = allData?.length ?? 0;
  const deleteMutation = useDeleteAttendance();

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

  function resetFilters() {
    setDateFrom("");
    setDateTo("");
    setSession("");
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

      <div className="mb-6 grid gap-4 lg:grid-cols-[1fr_auto]">
        <div className="rounded-2xl border border-border bg-paper p-4 text-sm text-slate">
          <p className="font-medium text-ink">Search historic attendance</p>
          <p className="mt-2">
            {total === 0
              ? "Use the filters to narrow the record set."
              : `Found ${total} matching record${total === 1 ? "" : "s"}.`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button variant="secondary" size="sm" onClick={resetFilters}>
            Clear filters
          </Button>
        </div>
      </div>

      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-[220px_220px_1fr]">
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
      </div>

      {isLoading && <Spinner label="Loading attendance records…" />}
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
                              const checkInDisplay = checkInDate ? formatClockTime(checkInDate) : "—";
                              const checkOutDisplay = checkOutDate ? formatClockTime(checkOutDate) : "—";
                              const closedDuration =
                                checkInDate && checkOutDate
                                  ? formatDuration(Math.max(0, Math.round((checkOutDate.getTime() - checkInDate.getTime()) / 60000)))
                                  : formatDuration(record.duration_minutes ?? 0);

                              return (
                                <Tr key={record.attendance_id}>
                                  <Td className="font-mono text-xs">{record.student_id}</Td>
                                  <Td>{formatDate(record.date)}</Td>
                                  <Td>
                                    <StatusTab tone={sessionTone(record.session)}>
                                      {record.session}
                                    </StatusTab>
                                  </Td>
                                  <Td className="font-mono text-xs">{checkInDisplay}</Td>
                                  <Td className="font-mono text-xs">
                                    {open ? <OpenSessionTime /> : checkOutDisplay || "—"}
                                  </Td>
                                  <Td className="text-slate">
                                    {open && checkInDate ? <OpenSessionDuration checkInDate={checkInDate} /> : closedDuration}
                                  </Td>
                                  <Td className="text-right">
                                    <div className="flex justify-end gap-1">
                                      <Button
                                        size="sm"
                                        variant="ghost"
                                        onClick={() => setEditing(record)}
                                      >
                                        <Pencil size={14} />
                                      </Button>
                                      <Button
                                        size="sm"
                                        variant="ghost"
                                        onClick={() => setDeleting(record)}
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
        onClose={() => setDeleting(undefined)}
        onConfirm={handleDelete}
        title="Delete attendance record"
        message="This removes the record permanently. Use Edit instead if you just need to fix a typo'd time."
        pending={deleteMutation.isPending}
      />
    </div>
  );
}

function sessionTone(session: string): "forest" | "brass" | "slate" {
  if (session === "Full Day") return "forest";
  if (session === "Morning") return "brass";
  return "slate";
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
