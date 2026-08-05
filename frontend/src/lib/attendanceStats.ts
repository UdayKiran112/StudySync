import { isLibraryHoliday, toIsoDate } from "./holidays";
import type { Attendance } from "../api/types";

export interface HolidayAwareAttendanceStats {
  /** Days in the window that the library was actually open (excludes holidays). */
  openDays: number;
  /** Of those open days, how many the student attended. */
  attendedDays: number;
  /** attendedDays / openDays, as a percentage. Null if the window has no open days. */
  ratePercent: number | null;
  /** Consecutive open days attended, walking back from today. Holidays are
   *  skipped entirely — a closed day never breaks (or extends) a streak. */
  streakDays: number;
}

/**
 * Recomputes attendance rate and streak the way the library actually runs:
 * Sundays and the 2nd/4th/5th Saturday of each month don't count against a
 * student either way, since the library isn't open for them to attend.
 */
export function computeHolidayAwareStats(
  history: Attendance[],
  windowDays = 30,
  referenceDate: Date = new Date()
): HolidayAwareAttendanceStats {
  const today = new Date(referenceDate);
  today.setHours(0, 0, 0, 0);
  const attendedDates = new Set(history.map((a) => a.date));

  // --- Rate over the trailing window ---
  let openDays = 0;
  let attendedDays = 0;
  for (let i = 0; i < windowDays; i++) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    if (isLibraryHoliday(d)) continue;
    openDays += 1;
    if (attendedDates.has(toIsoDate(d))) attendedDays += 1;
  }
  const ratePercent = openDays > 0 ? (attendedDays / openDays) * 100 : null;

  // --- Streak, walking backward from today ---
  let streakDays = 0;
  const todayIso = toIsoDate(today);
  const cursor = new Date(today);
  for (let guard = 0; guard < 3650; guard++) {
    if (isLibraryHoliday(cursor)) {
      cursor.setDate(cursor.getDate() - 1);
      continue;
    }
    const iso = toIsoDate(cursor);
    const attended = attendedDates.has(iso);
    if (attended) {
      streakDays += 1;
    } else if (iso !== todayIso) {
      // Today not having a record yet doesn't break the streak — the day
      // isn't over. Any earlier open day with no record does break it.
      break;
    }
    cursor.setDate(cursor.getDate() - 1);
  }

  return { openDays, attendedDays, ratePercent, streakDays };
}
