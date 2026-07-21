import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  BarChart3,
  ClipboardCheck,
  GraduationCap,
  ListChecks,
  Laptop,
  BookOpen,
  Presentation,
  Flame,
  CalendarClock,
  Search,
} from "lucide-react";
import { PageHeader, Spinner, ErrorBanner, EmptyState } from "../../components/ui/Feedback";
import { Field } from "../../components/ui/Form";
import { StudentPicker } from "../../components/ui/StudentPicker";
import { IdTab, StatusTab, studentStatusTone } from "../../components/ui/Tabs";
import { Table, Thead, Th, Tr, Td } from "../../components/ui/Table";
import { useStudentDashboard } from "../../api/dashboard";
import { extractErrorMessage } from "../../api/client";
import { formatDate, formatDuration } from "../../lib/format";
import type { Student } from "../../api/types";
import { AnalyticsStatCard } from "./components/AnalyticsStatCard";
import { TrendBadge } from "./components/TrendBadge";
import { ScoreTrendChart } from "./components/ScoreTrendChart";
import { SubjectPerformanceTable } from "./components/SubjectPerformanceTable";
import { CategoryBreakdownChart } from "./components/CategoryBreakdownChart";
import { AssessmentAttemptsTable } from "./components/AssessmentAttemptsTable";
import { ExportMenu } from "./components/ExportMenu";

export function StudentAnalyticsPage() {
  const { studentId } = useParams();
  const navigate = useNavigate();
  const id = studentId ? Number(studentId) : undefined;

  const [picked, setPicked] = useState<Student | null>(null);
  const { data: dashboard, isLoading, isError, error } = useStudentDashboard(id);

  // Keep the search box showing the loaded student once the report arrives
  // (covers landing directly on /analytics/:id via a link, not just search).
  useEffect(() => {
    if (dashboard?.student) setPicked(dashboard.student);
  }, [dashboard?.student]);

  function handlePick(student: Student | null) {
    setPicked(student);
    if (student) navigate(`/analytics/${student.student_id}`);
  }

  return (
    <div>
      <PageHeader
        eyebrow="Reports"
        title="Student analytics"
        description="Look up a student by name or ID to see their full attendance, library, and assessment performance."
      />

      <div className="no-print mb-6 max-w-md">
        <Field label="Student">
          <StudentPicker value={picked} onChange={handlePick} activeOnly={false} />
        </Field>
      </div>

      {!id && (
        <EmptyState
          title="Search for a student to get started"
          description="Type a name or student ID above to pull up their analytics report."
          action={<Search size={22} className="text-slate-light" />}
        />
      )}

      {id && isLoading && <Spinner label="Building report…" />}
      {id && isError && <ErrorBanner message={extractErrorMessage(error)} />}

      {id && dashboard && <Report dashboard={dashboard} />}
    </div>
  );
}

function Report({ dashboard }: { dashboard: import("../../api/types").StudentDashboardResponse }) {
  const { student, analytics } = dashboard;

  return (
    <div className="space-y-8">
      {/* Profile header */}
      <div className="print-break-avoid flex flex-wrap items-start justify-between gap-4 rounded-lg border border-border bg-card p-6">
        <div className="flex items-center gap-3">
          <IdTab>{student.student_id}</IdTab>
          <div>
            <h2 className="font-display text-xl font-semibold text-ink">{student.name}</h2>
            <p className="mt-0.5 text-xs text-slate">
              Joined {formatDate(student.join_date)}
              {student.phone && ` · ${student.phone}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <StatusTab tone={studentStatusTone(student.status)}>{student.status}</StatusTab>
          <ExportMenu dashboard={dashboard} />
        </div>
      </div>

      {/* Top-line summary */}
      <section>
        <h3 className="mb-3 font-display text-base font-semibold text-ink">Overview</h3>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          <AnalyticsStatCard
            icon={BarChart3}
            label="Overall average"
            value={analytics.overall.average_percentage != null ? `${analytics.overall.average_percentage.toFixed(1)}%` : "—"}
            sub={<TrendBadge trend={analytics.overall.trend} delta={analytics.overall.trend_delta_percentage_points} />}
          />
          <AnalyticsStatCard
            icon={ClipboardCheck}
            label="Attendance (30d)"
            value={
              analytics.attendance.attendance_rate_last_30_days_percent != null
                ? `${analytics.attendance.attendance_rate_last_30_days_percent.toFixed(0)}%`
                : "—"
            }
            sub={<span className="text-xs text-slate">{analytics.attendance.total_sessions} sessions total</span>}
          />
          <AnalyticsStatCard
            icon={Flame}
            label="Current streak"
            value={`${analytics.attendance.current_streak_days}d`}
            sub={
              <span className="text-xs text-slate">
                {analytics.attendance.days_since_last_visit != null
                  ? `Last visit ${analytics.attendance.days_since_last_visit}d ago`
                  : "No visits yet"}
              </span>
            }
          />
          <AnalyticsStatCard
            icon={CalendarClock}
            label="Avg. session length"
            value={formatDuration(analytics.attendance.average_duration_minutes ?? null)}
            sub={<TrendBadge trend={analytics.attendance.trend} delta={analytics.attendance.trend_delta_minutes} suffix="min" />}
          />
        </div>
      </section>

      {/* Exams / Quizzes summary */}
      <section>
        <h3 className="mb-3 font-display text-base font-semibold text-ink">Assessment performance</h3>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <AnalyticsStatCard
            icon={GraduationCap}
            label="Exams taken"
            value={analytics.exams.total_exams}
            sub={
              <>
                <span className="mr-2 font-mono text-xs text-slate">
                  {analytics.exams.average_percentage != null ? `${analytics.exams.average_percentage.toFixed(1)}% avg` : "—"}
                </span>
                <TrendBadge trend={analytics.exams.trend} delta={analytics.exams.trend_delta_percentage_points} />
              </>
            }
          />
          <AnalyticsStatCard
            icon={ListChecks}
            label="Quizzes taken"
            value={analytics.quizzes.total_quizzes}
            sub={
              <>
                <span className="mr-2 font-mono text-xs text-slate">
                  {analytics.quizzes.average_percentage != null ? `${analytics.quizzes.average_percentage.toFixed(1)}% avg` : "—"}
                </span>
                <TrendBadge trend={analytics.quizzes.trend} delta={analytics.quizzes.trend_delta_percentage_points} />
              </>
            }
          />
          <AnalyticsStatCard
            icon={BarChart3}
            label="Total assessments"
            value={analytics.overall.total_assessments}
          />
        </div>

        <div className="mt-4">
          <ScoreTrendChart data={dashboard.score_trend} />
        </div>

        <div className="mt-4">
          <h4 className="mb-2 text-sm font-medium text-slate">By subject</h4>
          <SubjectPerformanceTable subjects={analytics.subjects} />
        </div>
      </section>

      {/* Library usage */}
      <section>
        <h3 className="mb-3 font-display text-base font-semibold text-ink">Library usage</h3>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <AnalyticsStatCard
            icon={Laptop}
            label="Digital library time"
            value={formatDuration(analytics.digital_library.total_duration_minutes)}
            sub={<span className="text-xs text-slate">{analytics.digital_library.total_sessions} sessions</span>}
          />
          <AnalyticsStatCard
            icon={BookOpen}
            label="Offline visits"
            value={analytics.offline_library.total_sessions}
            sub={<span className="text-xs text-slate">{analytics.offline_library.self_study_sessions} self-study</span>}
          />
          <AnalyticsStatCard
            icon={BookOpen}
            label="Offline library time"
            value={formatDuration(analytics.offline_library.estimated_total_minutes)}
            sub={<span className="text-xs text-slate-light">Inferred from attendance minus digital time</span>}
          />
          <AnalyticsStatCard icon={Presentation} label="Coaching class time" value={formatDuration(analytics.coaching.total_duration_minutes)} sub={<span className="text-xs text-slate">{analytics.coaching.total_sessions} sessions</span>} />
        </div>

        <div className="mt-4">
          <h4 className="mb-2 text-sm font-medium text-slate">Offline library — by category</h4>
          <CategoryBreakdownChart data={analytics.offline_library.by_category} />
        </div>
      </section>

      {/* Detailed history */}
      <section>
        <h3 className="mb-3 font-display text-base font-semibold text-ink">Exams attempted</h3>
        <AssessmentAttemptsTable attempts={dashboard.exams_attempted} emptyLabel="No exams attempted yet" />
      </section>

      <section>
        <h3 className="mb-3 font-display text-base font-semibold text-ink">Quizzes attempted</h3>
        <AssessmentAttemptsTable attempts={dashboard.quizzes_attempted} emptyLabel="No quizzes attempted yet" />
      </section>

      <section>
        <h3 className="mb-3 font-display text-base font-semibold text-ink">Recent attendance</h3>
        {dashboard.attendance_history.length === 0 ? (
          <EmptyState title="No attendance recorded yet" />
        ) : (
          <Table>
            <Thead>
              <Th>Date</Th>
              <Th>Session</Th>
              <Th>Check-in</Th>
              <Th>Check-out</Th>
              <Th>Duration</Th>
            </Thead>
            <tbody>
              {dashboard.attendance_history.slice(0, 15).map((a) => (
                <Tr key={a.attendance_id}>
                  <Td>{formatDate(a.date)}</Td>
                  <Td>{a.session}</Td>
                  <Td className="font-mono text-xs">{a.check_in ?? "—"}</Td>
                  <Td className="font-mono text-xs">{a.check_out ?? <span className="text-brass">Still in</span>}</Td>
                  <Td className="text-slate">{formatDuration(a.duration_minutes)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </section>

      <section>
        <h3 className="mb-3 font-display text-base font-semibold text-ink">Recent library sessions</h3>
        {dashboard.digital_library_usage.length === 0 && dashboard.offline_library_usage.length === 0 ? (
          <EmptyState title="No library activity yet" />
        ) : (
          <Table>
            <Thead>
              <Th>Type</Th>
              <Th>Date</Th>
              <Th>Detail</Th>
              <Th>Duration</Th>
            </Thead>
            <tbody>
              {dashboard.digital_library_usage.slice(0, 10).map((u) => (
                <Tr key={`digital-${u.usage_id}`}>
                  <Td>Digital</Td>
                  <Td>{formatDate(u.date)}</Td>
                  <Td className="font-medium">{u.platform_name}</Td>
                  <Td className="text-slate">{formatDuration(u.duration_minutes)}</Td>
                </Tr>
              ))}
              {dashboard.offline_library_usage.slice(0, 10).map((u) => (
                <Tr key={`offline-${u.usage_id}`}>
                  <Td>Offline</Td>
                  <Td>{formatDate(u.date)}</Td>
                  <Td className="font-medium">{u.book_title ?? u.book_id ?? "Own material"}</Td>
                  <Td className="text-slate">—</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </section>
    </div>
  );
}
