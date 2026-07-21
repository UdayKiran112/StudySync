import { useMemo } from "react";
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Award, CalendarDays, HeartPulse, Trophy } from "lucide-react";
import type { StudentDashboardResponse } from "../../../api/types";
import { formatDuration } from "../../../lib/format";

const colours = ["#1e2a38", "#a9782f", "#5b7c71", "#a35d4e"];
const DAY = 86_400_000;
const asDay = (value: string) => new Date(`${value}T00:00:00`);
const key = (value: Date) => value.toISOString().slice(0, 10);

export function EngagementInsights({ dashboard }: { dashboard: StudentDashboardResponse }) {
  const data = useMemo(() => buildInsights(dashboard), [dashboard]);
  return <div className="space-y-8">
    <section>
      <h3 className="mb-3 font-display text-base font-semibold text-ink">Engagement overview</h3>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <InsightCard icon={Trophy} label="Engagement score" value={`${data.engagement}/100`} detail={data.engagement >= 80 ? "Excellent engagement" : data.engagement >= 55 ? "Building momentum" : "Needs support"} />
        <InsightCard icon={HeartPulse} label="Risk indicator" value={data.risk.label} detail={data.risk.detail} tone={data.risk.tone} />
        <InsightCard icon={CalendarDays} label="Consistency score" value={`${data.consistency}%`} detail="Active days in the last 30 days" />
        <InsightCard icon={Award} label="Lifetime study time" value={formatDuration(data.totalStudyMinutes)} detail={`${dashboard.analytics.overall.total_assessments} assessments completed`} />
      </div>
    </section>

    <section className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-lg border border-border bg-card p-5">
        <h3 className="font-display text-base font-semibold text-ink">Last 7 days</h3>
        <div className="mt-4 space-y-3 text-sm">
          <Metric label="Attendance" value={`${data.weekly.attendanceDays}/7 days`}><div className="flex gap-1">{data.weekly.days.map((day) => <span key={day.date} title={day.date} className={`h-3 flex-1 rounded-sm ${day.present ? "bg-brass" : "bg-paper-dim"}`} />)}</div></Metric>
          <Metric label="Digital library" value={formatDuration(data.weekly.digitalMinutes)} />
          <Metric label="Offline library" value={formatDuration(data.weekly.offlineMinutes)} />
          <Metric label="Coaching" value={formatDuration(data.weekly.coachingMinutes)} />
          <Metric label="Assessments completed" value={data.weekly.assessments.toString()} />
        </div>
      </div>
      <div className="rounded-lg border border-border bg-card p-5">
        <h3 className="font-display text-base font-semibold text-ink">Study-time distribution</h3>
        {data.distribution.some((item) => item.value > 0) ? <div className="mt-2 h-52"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={data.distribution} dataKey="value" nameKey="name" innerRadius={48} outerRadius={76} paddingAngle={2}>{data.distribution.map((item, index) => <Cell key={item.name} fill={colours[index]} />)}</Pie><Tooltip formatter={(value) => formatDuration(Number(value))} /><text x="50%" y="50%" textAnchor="middle" dominantBaseline="middle" className="fill-ink text-sm">Study time</text></PieChart></ResponsiveContainer></div> : <p className="mt-6 text-sm text-slate">Study time will appear after activity is recorded.</p>}
        <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-slate">{data.distribution.map((item, index) => <span key={item.name} className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: colours[index] }} />{item.name} ({item.percent}%)</span>)}</div>
      </div>
    </section>

    <section className="rounded-lg border border-border bg-card p-5"><h3 className="font-display text-base font-semibold text-ink">Coaching analytics</h3><div className="mt-3 grid gap-4 sm:grid-cols-4"><Metric label="Sessions attended" value={dashboard.analytics.coaching.total_sessions.toString()} /><Metric label="Average duration" value={formatDuration(dashboard.analytics.coaching.average_duration_minutes)} /><Metric label="Subjects attended" value={data.coaching.subjects || "—"} /><Metric label="Most attended instructor" value={data.coaching.instructor || "—"} /></div></section>

    <section className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-lg border border-border bg-card p-5"><h3 className="font-display text-base font-semibold text-ink">Monthly study hours</h3><div className="mt-3 h-56"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.months}><XAxis dataKey="month" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} unit="h" /><Tooltip formatter={(value) => [`${Number(value).toFixed(1)}h`, "Study time"]} /><Bar dataKey="hours" fill="#a9782f" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div></div>
      <div className="rounded-lg border border-border bg-card p-5"><h3 className="font-display text-base font-semibold text-ink">Performance and attendance</h3><div className="mt-5 grid grid-cols-2 gap-4"><div><p className="text-3xl font-semibold text-ink">{data.attendanceRate}%</p><p className="mt-1 text-sm text-slate">Attendance (30 days)</p></div><div><p className="text-3xl font-semibold text-ink">{data.averageScore == null ? "—" : `${data.averageScore}%`}</p><p className="mt-1 text-sm text-slate">Average marks</p></div></div><p className="mt-6 rounded-md bg-paper-dim px-3 py-2 text-sm text-slate">{data.performanceMessage}</p></div>
    </section>

    <section className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-lg border border-border bg-card p-5"><h3 className="font-display text-base font-semibold text-ink">Achievement badges</h3><div className="mt-3 flex flex-wrap gap-2">{data.badges.length ? data.badges.map((badge) => <span key={badge} className="rounded-full bg-paper-dim px-3 py-1.5 text-sm text-ink">{badge}</span>) : <p className="text-sm text-slate">Keep building activity to earn badges.</p>}</div></div>
      <div className="rounded-lg border border-border bg-card p-5"><h3 className="font-display text-base font-semibold text-ink">Subject strengths</h3>{data.subjects.length ? <div className="mt-3 space-y-2 text-sm">{data.subjects.map((subject) => { const score = subject.average_percentage ?? 0; return <div key={subject.subject} className="flex justify-between border-b border-border pb-2"><span className={score >= 75 ? "text-emerald-700" : score < 60 ? "text-rust" : "text-ink"}>{subject.subject}</span><span className="font-medium">{score.toFixed(1)}%</span></div>; })}</div> : <p className="mt-3 text-sm text-slate">Subject analysis appears after assessments are recorded.</p>}</div>
    </section>

    <section className="rounded-lg border border-border bg-card p-5"><h3 className="font-display text-base font-semibold text-ink">Attendance activity</h3><div className="mt-3 grid grid-cols-7 gap-1.5 sm:grid-cols-14">{data.heatmap.map((day) => <span key={day.date} title={`${day.date}: ${day.present ? "Present" : "No attendance"}`} className={`h-4 rounded-sm ${day.present ? "bg-brass" : "bg-paper-dim"}`} />)}</div><p className="mt-2 text-xs text-slate">Most recent 84 days · filled squares mark attendance.</p></section>

    <section><h3 className="mb-3 font-display text-base font-semibold text-ink">Recent activity</h3><div className="rounded-lg border border-border bg-card">{data.timeline.length ? data.timeline.map((item) => <div key={item.id} className="flex items-start justify-between gap-4 border-b border-border px-4 py-3 last:border-0"><div><p className="text-sm font-medium text-ink">{item.title}</p><p className="text-xs text-slate">{item.detail}</p></div><span className="whitespace-nowrap text-xs text-slate">{item.date}</span></div>) : <p className="p-4 text-sm text-slate">No recent activity yet.</p>}</div></section>
  </div>;
}

function InsightCard({ icon: Icon, label, value, detail, tone }: { icon: typeof Trophy; label: string; value: string; detail: string; tone?: "good" | "warn" | "risk" }) { return <div className="rounded-lg border border-border bg-card p-4"><Icon size={18} className={tone === "risk" ? "text-rust" : tone === "warn" ? "text-brass" : "text-brass"} /><p className="mt-3 text-2xl font-semibold text-ink">{value}</p><p className="mt-1 text-sm text-slate">{label}</p><p className="mt-1 text-xs text-slate-light">{detail}</p></div>; }
function Metric({ label, value, children }: { label: string; value: string; children?: React.ReactNode }) { return <div><div className="mb-1 flex justify-between"><span className="text-slate">{label}</span><span className="font-medium text-ink">{value}</span></div>{children}</div>; }

function buildInsights(dashboard: StudentDashboardResponse) {
  const { analytics, attendance_history: attendance, digital_library_usage: digital, coaching_usage: coaching, score_trend: assessments } = dashboard;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const dates = new Set(attendance.map((item) => item.date));
  const weeklyDays = Array.from({ length: 7 }, (_, index) => { const date = new Date(today.getTime() - (6 - index) * DAY); return { date: key(date), present: dates.has(key(date)) }; });
  const weekStart = new Date(today.getTime() - 6 * DAY);
  const inWeek = (date: string) => asDay(date) >= weekStart;
  const attendanceByDate = Object.fromEntries(attendance.map((item) => [item.date, item.duration_minutes ?? 0]));
  const digitalByDate = digital.reduce<Record<string, number>>((sum, item) => ({ ...sum, [item.date]: (sum[item.date] ?? 0) + (item.duration_minutes ?? 0) }), {});
  const coachingMinutes = analytics.coaching.total_duration_minutes;
  const offline = analytics.offline_library.estimated_total_minutes;
  const weeklyDigital = digital.filter((item) => inWeek(item.date)).reduce((sum, item) => sum + (item.duration_minutes ?? 0), 0);
  const weeklyAttendance = Object.entries(attendanceByDate).filter(([date]) => inWeek(date)).reduce((sum, [date, minutes]) => sum + Math.max(minutes - (digitalByDate[date] ?? 0), 0), 0);
  const weeklyCoaching = coaching.filter((item) => inWeek(item.date)).reduce((sum, item) => sum + (item.duration_minutes ?? 0), 0);
  const totalStudyMinutes = analytics.digital_library.total_duration_minutes + offline + coachingMinutes;
  const distribution = [["Digital library", analytics.digital_library.total_duration_minutes], ["Offline library", offline], ["Coaching", coachingMinutes], ["Assessments", assessments.length * 30]].map(([name, value]) => ({ name: String(name), value: Number(value), percent: totalStudyMinutes ? Math.round(Number(value) * 100 / (totalStudyMinutes + assessments.length * 30)) : 0 }));
  const months = Array.from({ length: 6 }, (_, index) => { const date = new Date(today.getFullYear(), today.getMonth() - (5 - index), 1); const prefix = key(date).slice(0, 7); const digitalMinutes = digital.filter((item) => item.date.startsWith(prefix)).reduce((sum, item) => sum + (item.duration_minutes ?? 0), 0); const presentMinutes = attendance.filter((item) => item.date.startsWith(prefix)).reduce((sum, item) => sum + Math.max((item.duration_minutes ?? 0) - (digitalByDate[item.date] ?? 0), 0), 0); return { month: date.toLocaleDateString(undefined, { month: "short" }), hours: Math.round((digitalMinutes + presentMinutes) / 6) / 10 }; });
  const activeDays = new Set([...attendance.map((item) => item.date), ...digital.map((item) => item.date)]);
  const consistency = Math.round(Array.from({ length: 30 }, (_, index) => key(new Date(today.getTime() - index * DAY))).filter((date) => activeDays.has(date)).length / 30 * 100);
  const attendanceRate = Math.round(analytics.attendance.attendance_rate_last_30_days_percent ?? 0);
  const averageScore = analytics.overall.average_percentage == null ? null : Math.round(analytics.overall.average_percentage);
  const engagement = Math.min(100, Math.round(attendanceRate * .35 + Math.min(100, consistency * 1.2) * .25 + Math.min(100, analytics.digital_library.total_sessions * 4) * .15 + Math.min(100, analytics.coaching.total_sessions * 8) * .1 + (averageScore ?? 0) * .15));
  const risk = attendanceRate < 40 || (averageScore != null && averageScore < 50) ? { label: "High risk", detail: "Attendance or marks need intervention", tone: "risk" as const } : attendanceRate < 70 || (averageScore != null && averageScore < 65) ? { label: "Needs attention", detail: "Monitor activity and performance", tone: "warn" as const } : { label: "On track", detail: "Healthy activity and performance", tone: "good" as const };
  const badges = [analytics.attendance.current_streak_days >= 30 && "🔥 30-day streak", analytics.digital_library.total_sessions >= 20 && "📚 Library champion", (averageScore ?? 0) >= 90 && "💯 90%+ average", attendanceRate >= 90 && "🎯 Excellent attendance", analytics.quizzes.average_percentage != null && analytics.quizzes.average_percentage >= 90 && "⚡ Quiz expert"].filter(Boolean) as string[];
  const subjects = [...analytics.subjects].sort((a, b) => (b.average_percentage ?? 0) - (a.average_percentage ?? 0));
  const heatmap = Array.from({ length: 84 }, (_, index) => { const date = new Date(today.getTime() - (83 - index) * DAY); return { date: key(date), present: dates.has(key(date)) }; });
  const timeline = [...attendance.slice(0, 8).map((item) => ({ id: `attendance-${item.attendance_id}`, title: "Attendance recorded", detail: item.duration_minutes ? formatDuration(item.duration_minutes) : "Checked in", date: item.date })), ...digital.slice(0, 8).map((item) => ({ id: `digital-${item.usage_id}`, title: "Digital library", detail: `${item.platform_name} · ${formatDuration(item.duration_minutes)}`, date: item.date })), ...assessments.slice(-8).map((item) => ({ id: `assessment-${item.assessment_type}-${item.assessment_id}`, title: `${item.assessment_type} completed`, detail: `${item.assessment_name} · ${item.percentage.toFixed(1)}%`, date: item.date ?? "—" }))].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 12);
  const performanceMessage = averageScore == null ? "Add assessment results to compare academic performance with attendance." : attendanceRate >= 80 && averageScore >= 75 ? "Strong positive: regular attendance is translating into good academic results." : attendanceRate >= 80 && averageScore < 65 ? "Good attendance, but academic support may be useful." : "Strengthen regular attendance and study time to improve results.";
  const subjectCounts = coaching.reduce<Record<string, number>>((sum, item) => item.subject ? { ...sum, [item.subject]: (sum[item.subject] ?? 0) + 1 } : sum, {});
  const instructorCounts = coaching.reduce<Record<string, number>>((sum, item) => item.instructor_name ? { ...sum, [item.instructor_name]: (sum[item.instructor_name] ?? 0) + 1 } : sum, {});
  const coachingSummary = { subjects: Object.keys(subjectCounts).join(", "), instructor: Object.entries(instructorCounts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "" };
  return { engagement, risk, consistency, totalStudyMinutes, weekly: { attendanceDays: weeklyDays.filter((day) => day.present).length, days: weeklyDays, digitalMinutes: weeklyDigital, offlineMinutes: weeklyAttendance, coachingMinutes: weeklyCoaching, assessments: assessments.filter((item) => item.date && inWeek(item.date)).length }, distribution, months, attendanceRate, averageScore, performanceMessage, badges, subjects, heatmap, timeline, coaching: coachingSummary };
}
