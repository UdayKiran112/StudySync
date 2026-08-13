import { useMemo } from "react";
import { isLibraryClosed, toIsoDate, HOLIDAY_RULE_DESCRIPTION } from "../../../lib/holidays";
import { formatDate } from "../../../lib/format";
import type { Attendance, Holiday } from "../../../api/types";

type DayStatus = "present" | "absent" | "holiday";

interface CalendarDay {
  date: Date;
  iso: string;
  status: DayStatus;
  /** Holiday name when this day is a staff-recorded closure, else undefined. */
  holidayName?: string;
}

const CELL = "h-3 w-3 rounded-sm";
const STATUS_CLASS: Record<DayStatus, string> = {
  present: `${CELL} bg-forest`,
  absent: `${CELL} border border-rust/40 bg-rust/15`,
  holiday: `${CELL} holiday-hatch border border-border`,
};
const STATUS_LABEL: Record<DayStatus, string> = {
  present: "Present",
  absent: "Absent",
  holiday: "Library holiday",
};

export function AttendanceCalendar({
  history,
  weeks = 12,
  holidays = [],
}: {
  history: Attendance[];
  weeks?: number;
  holidays?: Holiday[];
}) {
  const columns = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const attendedDates = new Set(history.map((a) => a.date));
    const extraHolidays = new Set(holidays.map((h) => h.holiday_date));
    const holidayNames = new Map(holidays.map((h) => [h.holiday_date, h.name]));

    const totalDays = weeks * 7;
    const start = new Date(today);
    start.setDate(start.getDate() - (totalDays - 1));
    start.setDate(start.getDate() - start.getDay()); // snap back to the preceding Sunday

    const days: CalendarDay[] = [];
    const cursor = new Date(start);
    while (cursor <= today) {
      const iso = toIsoDate(cursor);
      const status: DayStatus = isLibraryClosed(cursor, extraHolidays)
        ? "holiday"
        : attendedDates.has(iso)
          ? "present"
          : "absent";
      days.push({ date: new Date(cursor), iso, status, holidayName: holidayNames.get(iso) });
      cursor.setDate(cursor.getDate() + 1);
    }

    const grouped: CalendarDay[][] = [];
    for (let i = 0; i < days.length; i += 7) grouped.push(days.slice(i, i + 7));

    let lastMonth = -1;
    const monthLabels = grouped.map((week) => {
      const month = week[0].date.getMonth();
      if (month === lastMonth) return "";
      lastMonth = month;
      return week[0].date.toLocaleDateString(undefined, { month: "short" });
    });

    return { weeks: grouped, monthLabels };
  }, [history, weeks, holidays]);

  return (
    <div className="print-break-avoid rounded-lg border border-border bg-card p-4">
      <div className="overflow-x-auto scrollbar-thin">
        <div className="flex gap-1">
          {columns.weeks.map((week, wi) => (
            <div key={wi} className="flex flex-col gap-1">
              <span className="block h-3 text-xs leading-3 text-slate-light">{columns.monthLabels[wi]}</span>
              {week.map((day) => (
                <div
                  key={day.iso}
                  className={STATUS_CLASS[day.status]}
                  title={`${formatDate(day.iso)} — ${day.holidayName ?? STATUS_LABEL[day.status]}`}
                />
              ))}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-4 border-t border-border pt-3 text-xs text-slate">
        <span className="flex items-center gap-1.5">
          <span className={STATUS_CLASS.present} /> Present
        </span>
        <span className="flex items-center gap-1.5">
          <span className={STATUS_CLASS.absent} /> Absent
        </span>
        <span className="flex items-center gap-1.5">
          <span className={STATUS_CLASS.holiday} /> Library holiday
        </span>
        <span className="text-slate-light">({HOLIDAY_RULE_DESCRIPTION})</span>
      </div>
    </div>
  );
}
