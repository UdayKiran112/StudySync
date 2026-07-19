import { useState, useRef, useEffect } from "react";
import { Download, Printer, ChevronDown, FileSpreadsheet } from "lucide-react";
import { Button } from "../../../components/ui/Button";
import { downloadCsv } from "../../../lib/csvExport";
import type { StudentDashboardResponse } from "../../../api/types";

export function ExportMenu({ dashboard }: { dashboard: StudentDashboardResponse }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const fileBase = `${dashboard.student.student_id}-${dashboard.student.name.replace(/\s+/g, "_")}`;

  function exportAssessments() {
    const rows = [...dashboard.exams_attempted, ...dashboard.quizzes_attempted].map((a) => ({
      type: a.assessment_type,
      name: a.assessment_name,
      subject: a.subject,
      date: a.date,
      marks_obtained: a.marks_obtained,
      max_marks: a.max_marks,
      percentage: a.percentage.toFixed(1),
      batch_average_percentage: a.batch_average_percentage?.toFixed(1) ?? "",
      remarks: a.remarks,
    }));
    downloadCsv(`${fileBase}-assessments`, rows);
    setOpen(false);
  }

  function exportAttendance() {
    const rows = dashboard.attendance_history.map((a) => ({
      date: a.date,
      session: a.session,
      check_in: a.check_in,
      check_out: a.check_out,
      duration_minutes: a.duration_minutes,
    }));
    downloadCsv(`${fileBase}-attendance`, rows);
    setOpen(false);
  }

  function exportLibraryUsage() {
    const digital = dashboard.digital_library_usage.map((u) => ({
      type: "Digital",
      date: u.date,
      detail: u.platform_name,
      in_time: u.in_time,
      out_time: u.out_time,
      duration_minutes: u.duration_minutes,
    }));
    const offline = dashboard.offline_library_usage.map((u) => ({
      type: "Offline",
      date: u.date,
      detail: u.book_title ?? u.book_id ?? "Own material",
      in_time: "",
      out_time: "",
      duration_minutes: "",
    }));
    downloadCsv(`${fileBase}-library-usage`, [...digital, ...offline]);
    setOpen(false);
  }

  function exportSummary() {
    const a = dashboard.analytics;
    const rows = [
      { metric: "Overall average %", value: a.overall.average_percentage, trend: a.overall.trend },
      { metric: "Exam average %", value: a.exams.average_percentage, trend: a.exams.trend },
      { metric: "Quiz average %", value: a.quizzes.average_percentage, trend: a.quizzes.trend },
      { metric: "Attendance sessions", value: a.attendance.total_sessions, trend: a.attendance.trend },
      {
        metric: "Attendance rate (30d) %",
        value: a.attendance.attendance_rate_last_30_days_percent,
        trend: "",
      },
      { metric: "Current streak (days)", value: a.attendance.current_streak_days, trend: "" },
      { metric: "Digital library sessions", value: a.digital_library.total_sessions, trend: "" },
      { metric: "Offline library sessions", value: a.offline_library.total_sessions, trend: "" },
    ];
    downloadCsv(`${fileBase}-summary`, rows);
    setOpen(false);
  }

  return (
    <div className="relative no-print" ref={ref}>
      <Button variant="secondary" onClick={() => setOpen((v) => !v)}>
        <Download size={15} /> Export <ChevronDown size={13} />
      </Button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 w-64 overflow-hidden rounded-md border border-border bg-card shadow-lg">
          <button
            onClick={() => window.print()}
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm hover:bg-paper-dim"
          >
            <Printer size={15} className="text-brass" />
            <div>
              <p className="font-medium text-ink">Print / Save as PDF</p>
              <p className="text-xs text-slate-light">Full report, browser print dialog</p>
            </div>
          </button>
          <div className="border-t border-border" />
          <button onClick={exportSummary} className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm hover:bg-paper-dim">
            <FileSpreadsheet size={15} className="text-brass" />
            <span className="text-ink">Summary metrics (CSV)</span>
          </button>
          <button onClick={exportAssessments} className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm hover:bg-paper-dim">
            <FileSpreadsheet size={15} className="text-brass" />
            <span className="text-ink">Exams & quizzes (CSV)</span>
          </button>
          <button onClick={exportAttendance} className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm hover:bg-paper-dim">
            <FileSpreadsheet size={15} className="text-brass" />
            <span className="text-ink">Attendance history (CSV)</span>
          </button>
          <button onClick={exportLibraryUsage} className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm hover:bg-paper-dim">
            <FileSpreadsheet size={15} className="text-brass" />
            <span className="text-ink">Library usage (CSV)</span>
          </button>
        </div>
      )}
    </div>
  );
}
