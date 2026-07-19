import { Spinner, ErrorBanner, EmptyState } from "../../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../../components/ui/Table";
import { useDigitalLibraryList } from "../../../api/digitalLibrary";
import { extractErrorMessage } from "../../../api/client";
import { formatDate, formatDuration } from "../../../lib/format";

export function StudentDigitalLibraryTab({ studentId }: { studentId: number }) {
  const { data, isLoading, isError, error } = useDigitalLibraryList({ student_id: studentId, limit: 100 });

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
        {data.map((u) => (
          <Tr key={u.usage_id}>
            <Td>{formatDate(u.date)}</Td>
            <Td className="font-medium">{u.platform_name}</Td>
            <Td className="text-slate">{u.account_type === "Library Subscription" ? u.subscription_id : "Own account"}</Td>
            <Td className="font-mono text-xs">{u.in_time}</Td>
            <Td className="font-mono text-xs">{u.out_time ?? <span className="text-brass">Still in</span>}</Td>
            <Td className="text-slate">{formatDuration(u.duration_minutes)}</Td>
          </Tr>
        ))}
      </tbody>
    </Table>
  );
}
