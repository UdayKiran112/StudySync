import { Spinner, ErrorBanner, EmptyState } from "../../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../../components/ui/Table";
import { useOfflineLibraryList } from "../../../api/offlineLibrary";
import { extractErrorMessage } from "../../../api/client";
import { formatDate } from "../../../lib/format";

export function StudentOfflineLibraryTab({ studentId }: { studentId: number }) {
  const { data, isLoading, isError, error } = useOfflineLibraryList({ student_id: studentId, limit: 100 });

  if (isLoading) return <Spinner label="Loading offline library visits…" />;
  if (isError) return <ErrorBanner message={extractErrorMessage(error)} />;
  if (!data || data.length === 0) return <EmptyState title="No offline library visits logged yet" />;

  return (
    <Table>
      <Thead>
        <Th>Date</Th>
        <Th>Book</Th>
      </Thead>
      <tbody>
        {data.map((u) => (
          <Tr key={u.usage_id}>
            <Td>{formatDate(u.date)}</Td>
            <Td>{u.book_id ?? <span className="text-slate">Own material</span>}</Td>
          </Tr>
        ))}
      </tbody>
    </Table>
  );
}
