/**
 * The library's fixed holiday calendar:
 *   - every Sunday
 *   - the 2nd, 4th, and 5th Saturday of each month (a 5th Saturday only
 *     occurs a handful of times a year)
 *
 * All date math here works in LOCAL time deliberately — `new Date(isoString)`
 * parses "YYYY-MM-DD" as UTC midnight, which shifts a day in negative UTC
 * offsets. parseLocalDate avoids that.
 */

export function parseLocalDate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function toIsoDate(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** 1-indexed occurrence of this date's weekday within its month. */
export function weekdayOccurrenceInMonth(date: Date): number {
  return Math.ceil(date.getDate() / 7);
}

export function isLibraryHoliday(input: Date | string): boolean {
  const date = typeof input === "string" ? parseLocalDate(input) : input;
  const day = date.getDay(); // 0 = Sunday, 6 = Saturday
  if (day === 0) return true;
  if (day === 6) {
    const occurrence = weekdayOccurrenceInMonth(date);
    return occurrence === 2 || occurrence === 4 || occurrence === 5;
  }
  return false;
}

const NO_EXTRA_HOLIDAYS: ReadonlySet<string> = new Set();

/**
 * Is the library closed on this date? One-off closures recorded by staff
 * (the holidays API, e.g. a festival) are checked first, then the standing
 * rule above. This is the single predicate every analytics/calendar call
 * should use.
 */
export function isLibraryClosed(
  input: Date | string,
  extraHolidays: ReadonlySet<string> = NO_EXTRA_HOLIDAYS,
): boolean {
  const date = typeof input === "string" ? parseLocalDate(input) : input;
  if (extraHolidays.has(toIsoDate(date))) return true;
  return isLibraryHoliday(date);
}

export const HOLIDAY_RULE_DESCRIPTION = "Sundays, and the 2nd, 4th & 5th Saturdays of each month";
