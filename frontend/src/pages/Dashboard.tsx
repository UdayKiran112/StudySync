import { Link } from "react-router-dom";
import { Users, ClipboardCheck, Laptop, BookOpen, ArrowRight } from "lucide-react";
import { PageHeader, ErrorBanner } from "../components/ui/Feedback";
import { useAttendanceList } from "../api/attendance";
import { useDigitalLibraryList } from "../api/digitalLibrary";
import { useOfflineLibraryList } from "../api/offlineLibrary";
import { extractErrorMessage } from "../api/client";
import { todayIso } from "../lib/format";
import { useSettings } from "../context/SettingsContext";

export function Dashboard() {
  const { isConfigured } = useSettings();
  const today = todayIso();

  const attendance = useAttendanceList({ date_: today, limit: 200 });
  const digital = useDigitalLibraryList({ date_: today, limit: 200 });
  const offline = useOfflineLibraryList({ date_: today, limit: 200 });

  const attendanceOpen = attendance.data?.filter((a) => !a.check_out).length ?? 0;
  const digitalOpen = digital.data?.filter((u) => !u.out_time).length ?? 0;

  return (
    <div>
      <PageHeader
        eyebrow={new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
        title="Today at the front desk"
        description="A quick snapshot of who's here and what's happening — jump straight into the day's work."
      />

      {!isConfigured ? (
        <div className="rounded-lg border border-dashed border-border bg-card p-10 text-center">
          <p className="font-display text-lg text-ink">Connect StudySync to your backend</p>
          <p className="mt-2 text-sm text-slate">
            Head to Settings to enter your API base URL and staff key before the dashboard can load data.
          </p>
          <Link
            to="/settings"
            className="mt-4 inline-flex items-center gap-1.5 rounded-md bg-ink px-4 py-2 text-sm font-medium text-paper hover:bg-ink-light"
          >
            Go to settings <ArrowRight size={14} />
          </Link>
        </div>
      ) : (
        <>
          {(attendance.isError || digital.isError || offline.isError) && (
            <ErrorBanner
              message={extractErrorMessage(attendance.error ?? digital.error ?? offline.error)}
            />
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              icon={ClipboardCheck}
              label="Attendance sessions today"
              value={attendance.isLoading ? undefined : attendance.data?.length ?? 0}
              hint={`${attendanceOpen} still checked in`}
              to="/attendance"
            />
            <StatCard
              icon={Laptop}
              label="Digital library sessions today"
              value={digital.isLoading ? undefined : digital.data?.length ?? 0}
              hint={`${digitalOpen} still active`}
              to="/digital-library"
            />
            <StatCard
              icon={BookOpen}
              label="Offline library visits today"
              value={offline.isLoading ? undefined : offline.data?.length ?? 0}
              to="/offline-library"
            />
            <StatCard icon={Users} label="Manage students" value={undefined} hint="Search, add, edit records" to="/students" />
          </div>

          <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2">
            <QuickActionCard
              title="Check a student in or out"
              description="Record arrivals and departures for Morning and Afternoon sessions."
              to="/attendance"
              cta="Open attendance"
            />
            <QuickActionCard
              title="Start a digital library session"
              description="Log a student using JSTOR, Britannica Online, or another platform."
              to="/digital-library"
              cta="Open digital library"
            />
          </div>
        </>
      )}
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  hint,
  to,
}: {
  icon: typeof Users;
  label: string;
  value: number | undefined;
  hint?: string;
  to: string;
}) {
  return (
    <Link
      to={to}
      className="group rounded-lg border border-border bg-card p-5 transition-shadow hover:shadow-md"
    >
      <div className="flex items-center justify-between">
        <Icon size={18} className="text-brass" />
        <ArrowRight size={14} className="text-slate-light opacity-0 transition-opacity group-hover:opacity-100" />
      </div>
      <p className="mt-3 font-display text-3xl font-semibold text-ink">
        {value === undefined ? <span className="text-slate-light">···</span> : value}
      </p>
      <p className="mt-1 text-sm text-slate">{label}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-light">{hint}</p>}
    </Link>
  );
}

function QuickActionCard({
  title,
  description,
  to,
  cta,
}: {
  title: string;
  description: string;
  to: string;
  cta: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <p className="font-display text-base font-medium text-ink">{title}</p>
      <p className="mt-1 text-sm text-slate">{description}</p>
      <Link to={to} className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-brass hover:underline">
        {cta} <ArrowRight size={14} />
      </Link>
    </div>
  );
}
