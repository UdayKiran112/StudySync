import { Spinner, ErrorBanner, EmptyState } from "../../../components/ui/Feedback";
import { Table, Thead, Th, Tr, Td } from "../../../components/ui/Table";
import { useScoresForStudent, useQuizzes } from "../../../api/quizzes";
import { extractErrorMessage } from "../../../api/client";
import { formatDate } from "../../../lib/format";

export function StudentQuizzesTab({ studentId }: { studentId: number }) {
  const scores = useScoresForStudent(studentId);
  const quizzes = useQuizzes({ limit: 200 });

  if (scores.isLoading || quizzes.isLoading) return <Spinner label="Loading quiz scores…" />;
  if (scores.isError) return <ErrorBanner message={extractErrorMessage(scores.error)} />;
  if (!scores.data || scores.data.length === 0) return <EmptyState title="No quiz scores recorded yet" />;

  const quizById = new Map((quizzes.data ?? []).map((q) => [q.quiz_id, q]));

  return (
    <Table>
      <Thead>
        <Th>Quiz</Th>
        <Th>Subject</Th>
        <Th>Date</Th>
        <Th>Score</Th>
        <Th>Remarks</Th>
      </Thead>
      <tbody>
        {scores.data.map((s) => {
          const quiz = quizById.get(s.quiz_id);
          return (
            <Tr key={s.score_id}>
              <Td className="font-medium">{quiz?.quiz_name ?? `Quiz #${s.quiz_id}`}</Td>
              <Td className="text-slate">{quiz?.subject ?? "—"}</Td>
              <Td className="text-slate">{formatDate(quiz?.quiz_date)}</Td>
              <Td className="font-mono text-xs">
                {s.score} / {quiz?.max_marks ?? "—"}
              </Td>
              <Td className="text-slate">{s.remarks ?? "—"}</Td>
            </Tr>
          );
        })}
      </tbody>
    </Table>
  );
}
