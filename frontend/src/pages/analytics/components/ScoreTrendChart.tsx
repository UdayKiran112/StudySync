import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { EmptyState } from "../../../components/ui/Feedback";
import { formatDate } from "../../../lib/format";
import type { AssessmentAttempt } from "../../../api/types";

export function ScoreTrendChart({ data }: { data: AssessmentAttempt[] }) {
  if (data.length === 0) {
    return <EmptyState title="No score history yet" description="Percentages will chart here once exams or quizzes are recorded." />;
  }

  const chartData = data.map((a) => ({
    label: a.date ? formatDate(a.date) : a.assessment_name,
    name: a.assessment_name,
    percentage: Math.round(a.percentage * 10) / 10,
    batchAverage: a.batch_average_percentage != null ? Math.round(a.batch_average_percentage * 10) / 10 : undefined,
  }));

  return (
    <div className="print-break-avoid h-64 rounded-lg border border-border bg-card p-4">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 8, right: 16, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#dce1dd" />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#5b6472" }} />
          <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: "#5b6472" }} />
          <Tooltip
            contentStyle={{ borderRadius: 6, border: "1px solid #dce1dd", fontSize: 12 }}
            formatter={(value, name) => [
              `${value}%`,
              name === "Score" ? "Student score" : "Batch average",
            ]}
            labelFormatter={(_, payload) => payload?.[0]?.payload?.name ?? ""}
          />
          <Line type="monotone" dataKey="percentage" stroke="#1e2a38" strokeWidth={2} dot={{ r: 3 }} name="Score" />
          <Line
            type="monotone"
            dataKey="batchAverage"
            stroke="#a9782f"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            dot={false}
            name="Batch average"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
