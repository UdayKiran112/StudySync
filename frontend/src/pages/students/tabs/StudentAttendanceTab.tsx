import { useState, useEffect } from "react";
import {
  Spinner,
  ErrorBanner,
  EmptyState,
} from "../../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../../components/ui/Table";
import { useAttendanceList } from "../../../api/attendance";
import { extractErrorMessage } from "../../../api/client";
import { formatDate, formatDuration } from "../../../lib/format";

export function StudentAttendanceTab({ studentId }: { studentId: number }) {
  const { data, isLoading, isError, error } = useAttendanceList({
    student_id: studentId,
  });

  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (isLoading) return <Spinner label="Loading attendance…" />;
  if (isError) return <ErrorBanner message={extractErrorMessage(error)} />;
  if (!data || data.length === 0)
    return <EmptyState title="No attendance recorded yet" />;

  // The backend already orders newest-first and returns the full history
  // unbounded — cap what we render to keep this tab readable.
  const recent = data.slice(0, 100);

  return (
    <Table>
      <Thead>
        <Th>Date</Th>
        <Th>Session</Th>
        <Th>Check-in</Th>
        <Th>Check-out</Th>
        <Th>Duration</Th>
      </Thead>
      <tbody>
        {recent.map((a) => {
          const checkInDate = a.check_in ? new Date(`${a.date}T${a.check_in}`) : null;
          const checkOutDate = a.check_out ? new Date(`${a.date}T${a.check_out}`) : null;
          const effectiveCheckOutDate = checkOutDate ?? now;
          const checkInDisplay = checkInDate
            ? checkInDate.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
            : "—";
          const checkOutDisplay = checkOutDate
            ? checkOutDate.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
            : (checkInDate ? now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");
          const durationMinutes = checkInDate
            ? Math.max(0, Math.round((effectiveCheckOutDate.getTime() - checkInDate.getTime()) / 60000))
            : (a.duration_minutes ?? 0);

          return (
            <Tr key={a.attendance_id}>
              <Td>{formatDate(a.date)}</Td>
              <Td>{a.session}</Td>
              <Td className="font-mono text-xs">{checkInDisplay}</Td>
              <Td className="font-mono text-xs">{a.check_out ? checkOutDisplay : <span className="text-brass">{checkOutDisplay}</span>}</Td>
              <Td className="text-slate">{formatDuration(durationMinutes)}</Td>
            </Tr>
          );
        })}
      </tbody>
    </Table>
  );
}
