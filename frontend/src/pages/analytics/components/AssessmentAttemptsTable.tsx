import { Table, Thead, Th, Tr, Td } from "../../../components/ui/Table";
import { EmptyState } from "../../../components/ui/Feedback";
import { formatDate } from "../../../lib/format";
import type { AssessmentAttempt } from "../../../api/types";

export function AssessmentAttemptsTable({ attempts, emptyLabel }: { attempts: AssessmentAttempt[]; emptyLabel: string }) {
  if (attempts.length === 0) {
    return <EmptyState title={emptyLabel} />;
  }

  return (
    <Table>
      <Thead>
        <Th>Name</Th>
        <Th>Subject</Th>
        <Th>Date</Th>
        <Th>Score</Th>
        <Th>vs. batch avg</Th>
        <Th>Remarks</Th>
      </Thead>
      <tbody>
        {attempts.map((a) => {
          const diff =
            a.batch_average_percentage != null ? a.percentage - a.batch_average_percentage : null;
          return (
            <Tr key={`${a.assessment_type}-${a.assessment_id}`}>
              <Td className="font-medium">{a.assessment_name}</Td>
              <Td className="text-slate">{a.subject ?? "—"}</Td>
              <Td className="text-slate">{formatDate(a.date)}</Td>
              <Td className="font-mono text-xs">
                {a.marks_obtained} / {a.max_marks} ({a.percentage.toFixed(1)}%)
              </Td>
              <Td className="font-mono text-xs">
                {diff === null ? (
                  "—"
                ) : (
                  <span className={diff >= 0 ? "text-forest" : "text-rust"}>
                    {diff >= 0 ? "+" : ""}
                    {diff.toFixed(1)} pts
                  </span>
                )}
              </Td>
              <Td className="text-slate">{a.remarks ?? "—"}</Td>
            </Tr>
          );
        })}
      </tbody>
    </Table>
  );
}
