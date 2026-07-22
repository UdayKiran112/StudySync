import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  CalendarPlus,
  CircleUserRound,
  Users,
  UserCheck,
  UserMinus,
  UserRoundCheck,
} from "lucide-react";
import type { StudentSummary } from "../../api/types";
import { AnalyticsStatCard } from "../analytics/components/AnalyticsStatCard";

const colours = ["#1e2a38", "#a9782f", "#5b7c71", "#a35d4e"];

export function StudentSummaryDashboard({
  summary,
  loading,
}: {
  summary?: StudentSummary;
  loading: boolean;
}) {
  const registrations =
    summary?.monthly_registrations.map((item) => ({
      ...item,
      label: new Date(`${item.month}-01T00:00:00`).toLocaleDateString(
        undefined,
        { month: "short" },
      ),
    })) ?? [];
  const cardClass = "min-h-32 p-5";

  return (
    <section className="mb-6 space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <AnalyticsStatCard className={cardClass} to="/students" icon={Users} label="Total students" value={loading ? "…" : summary?.total ?? 0} />
        <AnalyticsStatCard className={cardClass} to="/students?view=active" icon={UserCheck} label="Active students" value={loading ? "…" : summary?.active ?? 0} />
        <AnalyticsStatCard className={cardClass} to="/students?view=inactive" icon={UserMinus} label="Inactive students" value={loading ? "…" : summary?.inactive ?? 0} />
        <AnalyticsStatCard className={cardClass} to="/students?view=new" icon={CalendarPlus} label="New this month" value={loading ? "…" : summary?.new_this_month ?? 0} />
        <AnalyticsStatCard className={cardClass} to="/students?view=expiring" icon={CircleUserRound} label="Expiring in 30 days" value={loading ? "…" : summary?.expiring ?? 0} />
        <AnalyticsStatCard className={cardClass} to="/students?view=present" icon={UserRoundCheck} label="Present today" value={loading ? "…" : summary?.present_today ?? 0} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="h-64 rounded-lg border border-border bg-card p-4">
          <h2 className="font-display text-base font-semibold text-ink">Monthly registrations</h2>
          {registrations.length ? (
            <ResponsiveContainer width="100%" height="88%">
              <BarChart data={registrations}>
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#5b6472" }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#5b6472" }} />
                <Tooltip formatter={(value) => [value, "Students"]} />
                <Bar dataKey="count" fill="#a9782f" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <p className="mt-8 text-sm text-slate">No registrations in the last six months for this filter.</p>}
        </div>
        <div className="h-64 rounded-lg border border-border bg-card p-4">
          <h2 className="font-display text-base font-semibold text-ink">Gender distribution</h2>
          {summary?.gender_distribution.length ? (
            <>
              <ResponsiveContainer width="100%" height="80%">
                <PieChart>
                  <Pie data={summary.gender_distribution} dataKey="count" nameKey="gender" innerRadius={42} outerRadius={70} paddingAngle={2}>
                    {summary.gender_distribution.map((item, index) => <Cell key={item.gender} fill={colours[index % colours.length]} />)}
                  </Pie>
                  <Tooltip formatter={(value) => [value, "Students"]} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-slate">
                {summary.gender_distribution.map((item, index) => <span key={item.gender} className="flex items-center gap-1"><i className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: colours[index % colours.length] }} />{item.gender} ({item.count})</span>)}
              </div>
            </>
          ) : <p className="mt-8 text-sm text-slate">No gender data available for this filter.</p>}
        </div>
      </div>
    </section>
  );
}
