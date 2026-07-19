import { Table, Thead, Th, Tr, Td } from "../../../components/ui/Table";
import { EmptyState } from "../../../components/ui/Feedback";
import { TrendBadge } from "./TrendBadge";
import type { SubjectPerformance } from "../../../api/types";

export function SubjectPerformanceTable({ subjects }: { subjects: SubjectPerformance[] }) {
  if (subjects.length === 0) {
    return <EmptyState title="No subject data yet" />;
  }

  return (
    <Table>
      <Thead>
        <Th>Subject</Th>
        <Th>Assessments</Th>
        <Th>Average</Th>
        <Th>Trend</Th>
      </Thead>
      <tbody>
        {subjects.map((s) => (
          <Tr key={s.subject}>
            <Td className="font-medium">{s.subject}</Td>
            <Td className="text-slate">{s.total_assessments}</Td>
            <Td className="font-mono text-xs">
              {s.average_percentage != null ? `${s.average_percentage.toFixed(1)}%` : "—"}
            </Td>
            <Td>
              <TrendBadge trend={s.trend} delta={s.trend_delta_percentage_points} />
            </Td>
          </Tr>
        ))}
      </tbody>
    </Table>
  );
}
