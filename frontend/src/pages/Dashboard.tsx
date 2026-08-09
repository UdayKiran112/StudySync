import { Link } from "react-router-dom";
import {
  Users,
  ClipboardCheck,
  Laptop,
  BookOpen,
  ArrowRight,
} from "lucide-react";
import {
  PageHeader,
  ErrorBanner,
  CardSkeleton,
  Skeleton,
} from "../components/ui/Feedback";
import { useAttendanceList } from "../api/attendance";
import { useDigitalLibraryList } from "../api/digitalLibrary";
import { useOfflineLibraryList } from "../api/offlineLibrary";
import { useCurrentlyPresent } from "../api/dashboard";
import { extractErrorMessage } from "../api/client";
import { formatClockTime, todayIso } from "../lib/format";
import { useSettings } from "../context/SettingsContext";
import { IdTab } from "../components/ui/Tabs";
import { OpenSessionDuration } from "../components/ui/LiveClock";
import type { PresentItem } from "../api/types";

export function Dashboard() {
  const { isConfigured } = useSettings();
  const today = todayIso();

  const attendance = useAttendanceList({
    date_from: today,
    date_to: today,
    limit: 500,
  });
  const digital = useDigitalLibraryList({ date_: today, limit: 200 });
  const offline = useOfflineLibraryList({ date_: today, limit: 200 });
  const present = useCurrentlyPresent();

  const attendanceOpen =
    attendance.data?.items.filter((a) => !a.check_out).length ?? 0;
  const digitalOpen = digital.data?.filter((u) => !u.out_time).length ?? 0;

  return (
    <div>
      <PageHeader
        eyebrow={new Date().toLocaleDateString(undefined, {
          weekday: "long",
          month: "long",
          day: "numeric",
        })}
        title="Today at the front desk"
        description="A quick snapshot of who's here and what's happening — jump straight into the day's work."
      />

      {!isConfigured ? (
        <div className="rounded-lg border border-dashed border-border bg-card p-10 text-center">
          <p className="font-display text-lg text-ink">
            Connect StudySync to your backend
          </p>
          <p className="mt-2 text-sm text-slate">
            Head to Settings to enter your API base URL and staff key before the
            dashboard can load data.
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
              message={extractErrorMessage(
                attendance.error ?? digital.error ?? offline.error,
              )}
            />
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <StatCard
              icon={ClipboardCheck}
              label="Attendance sessions today"
              value={
                attendance.isLoading
                  ? undefined
                  : (attendance.data?.items.length ?? 0)
              }
              hint={`${attendanceOpen} still checked in`}
              to="/attendance"
            />
            <StatCard
              icon={Laptop}
              label="Digital library sessions today"
              value={
                digital.isLoading ? undefined : (digital.data?.length ?? 0)
              }
              hint={`${digitalOpen} still active`}
              to="/digital-library"
            />
            <StatCard
              icon={BookOpen}
              label="Offline library visits today"
              value={
                offline.isLoading ? undefined : (offline.data?.length ?? 0)
              }
              to="/offline-library"
            />
          </div>

          <h2 className="mt-8 mb-3 font-display text-base font-semibold text-ink">
            Currently in the library
          </h2>
          <div className="rounded-lg border border-border bg-card">
            {present.isLoading ? (
              <div className="space-y-3 p-5">
                <Skeleton className="h-5 w-2/3" />
                <Skeleton className="h-5 w-1/2" />
                <Skeleton className="h-5 w-3/5" />
              </div>
            ) : present.isError ? (
              <p className="p-5 text-sm text-rust">
                Couldn't load the live list — refresh to try again.
              </p>
            ) : (present.data ?? []).length === 0 ? (
              <p className="p-5 text-sm text-slate">
                No one is currently checked in. When a student arrives, check
                them in from the{" "}
                <Link
                  to="/attendance"
                  className="font-medium text-brass hover:underline"
                >
                  attendance
                </Link>{" "}
                page.
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {present.data?.map((item) => (
                  <PresentRow key={`${item.activity}-${item.student_id}`} item={item} />
                ))}
              </ul>
            )}
          </div>

          <h2 className="mt-8 mb-3 font-display text-base font-semibold text-ink">
            Quick access
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
            <QuickActionCard
              title="Log an offline library visit"
              description="Record a student reading a catalogued book or their own material."
              to="/offline-library"
              cta="Open offline library"
            />
            <QuickActionCard
              title="Manage students"
              description="Search, add, edit, and renew student records."
              to="/students"
              cta="Open students"
            />
          </div>
        </>
      )}
    </div>
  );
}

function PresentRow({ item }: { item: PresentItem }) {
  const checkInDate =
    item.time != null ? new Date(`${item.date}T${item.time}`) : null;
  return (
    <li>
      <Link
        to={`/students/${item.student_id}`}
        className="flex flex-wrap items-center gap-3 px-5 py-3 transition-colors hover:bg-paper-dim"
      >
        <span
          className={
            item.activity === "attendance"
              ? "rounded-full bg-brass/15 px-2.5 py-1 text-xs font-medium text-brass"
              : "rounded-full bg-forest/15 px-2.5 py-1 text-xs font-medium text-forest"
          }
        >
          {item.activity === "attendance" ? "Present" : "Digital library"}
        </span>
        <IdTab>{item.student_id}</IdTab>
        <span className="flex-1 min-w-0 text-sm font-medium text-ink">
          {item.name}
        </span>
        <span className="flex items-center gap-3 text-sm text-slate">
          {checkInDate && (
            <span className="tabular-nums">
              in at {formatClockTime(checkInDate)}
            </span>
          )}
          {checkInDate && <OpenSessionDuration checkInDate={checkInDate} />}
        </span>
      </Link>
    </li>
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
  if (value === undefined) return <CardSkeleton />;
  return (
    <Link
      to={to}
      className="group rounded-lg border border-border bg-card p-5 transition-shadow hover:shadow-md"
    >
      <div className="flex items-center justify-between">
        <Icon size={18} className="text-brass" />
        <ArrowRight
          size={14}
          className="text-slate-light opacity-0 transition-opacity group-hover:opacity-100"
        />
      </div>
      <p className="mt-3 font-display text-3xl font-semibold tabular-nums text-ink">
        {value}
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
      <Link
        to={to}
        className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-brass hover:underline"
      >
        {cta} <ArrowRight size={14} />
      </Link>
    </div>
  );
}
