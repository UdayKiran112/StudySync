import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  Mail,
  Phone,
  MapPin,
  Cake,
  CalendarDays,
  BarChart3,
  Users,
  GraduationCap,
  Target,
  Compass,
  RefreshCw,
  Pencil,
} from "lucide-react";
import { Spinner, ErrorBanner } from "../../components/ui/Feedback";
import { StatusTab, studentStatusTone, IdTab } from "../../components/ui/Tabs";
import { Button } from "../../components/ui/Button";
import { useStudent } from "../../api/students";
import { extractErrorMessage } from "../../api/client";
import { formatDate, daysUntil } from "../../lib/format";
import { StudentAttendanceTab } from "./tabs/StudentAttendanceTab";
import { StudentDigitalLibraryTab } from "./tabs/StudentDigitalLibraryTab";
import { StudentOfflineLibraryTab } from "./tabs/StudentOfflineLibraryTab";
import { StudentExamsTab } from "./tabs/StudentExamsTab";
import { StudentQuizzesTab } from "./tabs/StudentQuizzesTab";
import { StudentFormModal } from "./StudentFormModal";
import clsx from "clsx";

const TABS = [
  { key: "attendance", label: "Attendance" },
  { key: "digital", label: "Digital library" },
  { key: "offline", label: "Offline library" },
  { key: "exams", label: "Exam marks" },
  { key: "quizzes", label: "Quiz scores" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export function StudentDetail() {
  const { studentId } = useParams();
  const id = Number(studentId);
  const { data: student, isLoading, isError, error } = useStudent(id);
  const [tab, setTab] = useState<TabKey>("attendance");
  const [editOpen, setEditOpen] = useState(false);

  if (isLoading) return <Spinner label="Loading student…" />;
  if (isError || !student)
    return <ErrorBanner message={extractErrorMessage(error)} />;

  const expiryDays = student.valid_until ? daysUntil(student.valid_until) : null;

  return (
    <div>
      <Link
        to="/students"
        className="mb-4 flex items-center gap-1.5 text-sm text-slate hover:text-ink"
      >
        <ArrowLeft size={15} /> Back to students
      </Link>

      <div className="mb-6 rounded-lg border border-border bg-card p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <IdTab>{student.student_id}</IdTab>
            <div>
              <h1 className="font-display text-2xl font-semibold text-ink">
                {student.name}
              </h1>
              <div className="mt-1 flex items-center gap-2 text-xs text-slate">
                <CalendarDays size={13} /> Joined{" "}
                {formatDate(student.join_date)}
                {student.gender && <span>· {student.gender}</span>}
                {student.valid_until && (
                  <span className="flex items-center gap-1">
                    · <RefreshCw size={12} /> Valid until{" "}
                    {formatDate(student.valid_until)}
                    {student.renewal_count > 0 &&
                      ` (renewed ${student.renewal_count}×)`}
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setEditOpen(true)}
            >
              <Pencil size={14} /> Edit
            </Button>
            <Link
              to={`/analytics/${student.student_id}`}
              className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-ink hover:bg-paper-dim"
            >
              <BarChart3 size={15} /> View analytics
            </Link>
            <StatusTab tone={studentStatusTone(student.status)}>
              {student.status}
            </StatusTab>
          </div>
        </div>

        {student.status === "Active" &&
          expiryDays != null &&
          expiryDays <= 30 && (
            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-md border border-brass/40 bg-brass/10 px-3 py-2 text-sm text-ink">
              <RefreshCw size={14} className="text-brass" />
              <span>
                Membership {expiryDays < 0 ? "expired" : "expires"} in{" "}
                {expiryDays < 0
                  ? `${Math.abs(expiryDays)} day${Math.abs(expiryDays) === 1 ? "" : "s"}`
                  : `${expiryDays} day${expiryDays === 1 ? "" : "s"}`}
                {" · "}
                {formatDate(student.valid_until)}
              </span>
              <span className="text-xs text-slate">
                Renew from the{" "}
                <Link to="/students" className="font-medium text-brass hover:underline">
                  students list
                </Link>{" "}
                to keep the ID active.
              </span>
            </div>
          )}

        <div className="mt-5 grid grid-cols-1 gap-3 border-t border-border pt-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <InfoItem icon={Phone} label="Phone" value={student.phone} />
          <InfoItem icon={Mail} label="Email" value={student.email} />
          <InfoItem icon={MapPin} label="Address" value={student.address} />
          <InfoItem
            icon={Cake}
            label="Date of birth"
            value={formatDate(student.date_of_birth)}
          />
          <InfoItem
            icon={Users}
            label="Father's name"
            value={student.father_name}
          />
          <InfoItem
            icon={GraduationCap}
            label="Qualification"
            value={student.qualification}
          />
          <InfoItem icon={Target} label="Goal" value={student.goal} />
          <InfoItem
            icon={Compass}
            label="Preparing for"
            value={student.preparing_for}
          />
        </div>
      </div>

      <div className="mb-4 flex gap-1 overflow-x-auto border-b border-border scrollbar-thin">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={clsx(
              "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              tab === t.key
                ? "border-brass text-ink"
                : "border-transparent text-slate hover:text-ink",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "attendance" && <StudentAttendanceTab studentId={id} />}
      {tab === "digital" && <StudentDigitalLibraryTab studentId={id} />}
      {tab === "offline" && <StudentOfflineLibraryTab studentId={id} />}
      {tab === "exams" && <StudentExamsTab studentId={id} />}
      {tab === "quizzes" && <StudentQuizzesTab studentId={id} />}

      <StudentFormModal
        key={student.student_id}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        student={student}
      />

      <p className="mt-6 text-xs text-slate-light">
        Looking for exams or quizzes catalog management? See{" "}
        <Link to="/exams" className="underline underline-offset-2">
          Exams
        </Link>{" "}
        or{" "}
        <Link to="/quizzes" className="underline underline-offset-2">
          Quizzes
        </Link>
        .
      </p>
    </div>
  );
}

function InfoItem({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Phone;
  label: string;
  value: string | null | undefined;
}) {
  return (
    <div className="flex items-start gap-2">
      <Icon size={15} className="mt-0.5 shrink-0 text-slate-light" />
      <div>
        <p className="text-xs uppercase tracking-wide text-slate-light">
          {label}
        </p>
        <p className="text-ink">{value || "—"}</p>
      </div>
    </div>
  );
}
