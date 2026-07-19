import { Spinner, ErrorBanner, EmptyState } from "../../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../../components/ui/Table";
import { useMarksForStudent, useExams } from "../../../api/exams";
import { extractErrorMessage } from "../../../api/client";
import { formatDate } from "../../../lib/format";

export function StudentExamsTab({ studentId }: { studentId: number }) {
  const marks = useMarksForStudent(studentId);
  const exams = useExams({ limit: 200 });

  if (marks.isLoading || exams.isLoading) return <Spinner label="Loading exam marks…" />;
  if (marks.isError) return <ErrorBanner message={extractErrorMessage(marks.error)} />;
  if (!marks.data || marks.data.length === 0) return <EmptyState title="No exam marks recorded yet" />;

  const examById = new Map((exams.data ?? []).map((e) => [e.exam_id, e]));

  return (
    <Table>
      <Thead>
        <Th>Exam</Th>
        <Th>Subject</Th>
        <Th>Date</Th>
        <Th>Marks</Th>
        <Th>Remarks</Th>
      </Thead>
      <tbody>
        {marks.data.map((m) => {
          const exam = examById.get(m.exam_id);
          return (
            <Tr key={m.mark_id}>
              <Td className="font-medium">{exam?.exam_name ?? `Exam #${m.exam_id}`}</Td>
              <Td className="text-slate">{exam?.subject ?? "—"}</Td>
              <Td className="text-slate">{formatDate(exam?.exam_date)}</Td>
              <Td className="font-mono text-xs">
                {m.marks_obtained} / {exam?.max_marks ?? "—"}
              </Td>
              <Td className="text-slate">{m.remarks ?? "—"}</Td>
            </Tr>
          );
        })}
      </tbody>
    </Table>
  );
}
