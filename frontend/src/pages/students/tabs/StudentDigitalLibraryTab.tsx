import { useState, useEffect } from "react";
import { Spinner, ErrorBanner, EmptyState } from "../../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../../components/ui/Table";
import { useDigitalLibraryList } from "../../../api/digitalLibrary";
import { extractErrorMessage } from "../../../api/client";
import { formatDate, formatDuration, formatClockHM } from "../../../lib/format";

export function StudentDigitalLibraryTab({ studentId }: { studentId: number }) {
  const { data, isLoading, isError, error } = useDigitalLibraryList({ student_id: studentId, limit: 100 });

  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (isLoading) return <Spinner label="Loading digital library sessions…" />;
  if (isError) return <ErrorBanner message={extractErrorMessage(error)} />;
  if (!data || data.length === 0) return <EmptyState title="No digital library sessions yet" />;

  return (
    <Table>
      <Thead>
        <Th>Date</Th>
        <Th>Platform</Th>
        <Th>Account</Th>
        <Th>In</Th>
        <Th>Out</Th>
        <Th>Duration</Th>
      </Thead>
      <tbody>
        {data.map((u) => {
          const inDate = u.in_time ? new Date(`${u.date}T${u.in_time}`) : null;
          const outDate = u.out_time ? new Date(`${u.date}T${u.out_time}`) : null;
          const effectiveOutDate = outDate ?? now;
          const inDisplay = inDate ? formatClockHM(inDate) : "—";
          const outDisplay = outDate ? formatClockHM(outDate) : "--";
          const durationMinutes = inDate
            ? Math.max(0, Math.round((effectiveOutDate.getTime() - inDate.getTime()) / 60000))
            : (u.duration_minutes ?? 0);

          return (
            <Tr key={u.usage_id}>
              <Td>{formatDate(u.date)}</Td>
              <Td className="font-medium">{u.platform_name}</Td>
              <Td className="text-slate">{u.account_type === "Library Subscription" ? u.subscription_id : "Own account"}</Td>
              <Td className="font-mono text-xs">{inDisplay}</Td>
              <Td className="font-mono text-xs">{outDisplay}</Td>
              <Td className="text-slate">{formatDuration(durationMinutes)}</Td>
            </Tr>
          );
        })}
      </tbody>
    </Table>
  );
}
