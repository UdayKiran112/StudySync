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
        {recent.map((a) => (
          <Tr key={a.attendance_id}>
            <Td>{formatDate(a.date)}</Td>
            <Td>{a.session}</Td>
            <Td className="font-mono text-xs">{a.check_in ?? "—"}</Td>
            <Td className="font-mono text-xs">
              {a.check_out ?? <span className="text-brass">Still in</span>}
            </Td>
            <Td className="text-slate">{formatDuration(a.duration_minutes)}</Td>
          </Tr>
        ))}
      </tbody>
    </Table>
  );
}
