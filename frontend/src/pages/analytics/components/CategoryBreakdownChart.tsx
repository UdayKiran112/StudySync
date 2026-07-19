import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { EmptyState } from "../../../components/ui/Feedback";
import type { BookCategoryCount } from "../../../api/types";

export function CategoryBreakdownChart({ data }: { data: BookCategoryCount[] }) {
  if (data.length === 0) {
    return <EmptyState title="No categorized offline library visits yet" />;
  }

  return (
    <div className="print-break-avoid h-56 rounded-lg border border-border bg-card p-4">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#dce1dd" horizontal={false} />
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: "#5b6472" }} />
          <YAxis type="category" dataKey="category" width={100} tick={{ fontSize: 11, fill: "#5b6472" }} />
          <Tooltip contentStyle={{ borderRadius: 6, border: "1px solid #dce1dd", fontSize: 12 }} />
          <Bar dataKey="count" fill="#a9782f" radius={[0, 3, 3, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
