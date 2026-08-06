export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value + "T00:00:00");
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Wall-clock HH:MM:SS for a Date, using the browser's locale. */
export function formatClockTime(date: Date): string {
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDuration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "—";
  // Averages (e.g. "Average session length") can come through with long
  // floating-point tails — round to whole minutes so output is like "2h 41m".
  const rounded = Math.round(minutes);
  const h = Math.floor(rounded / 60);
  const m = rounded % 60;
  if (h === 0) return `${m}m`;
  return `${h}h ${m}m`;
}

const IST_TIME_ZONE = "Asia/Kolkata";

/**
 * Reads a Date's wall-clock fields in IST, regardless of what timezone the
 * browser/OS is set to. Front-desk laptops aren't always configured for IST,
 * and `toISOString()` returns UTC — either of which silently shifts the
 * "today" boundary by up to 5.5 hours. Anchoring explicitly to IST avoids
 * both.
 */
function istParts(date: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: IST_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string) =>
    parts.find((p) => p.type === type)?.value ?? "00";
  return {
    year: get("year"),
    month: get("month"),
    day: get("day"),
    hour: get("hour"),
    minute: get("minute"),
  };
}

/** Current (or given) date as YYYY-MM-DD in IST. */
export function todayIso(date: Date = new Date()): string {
  const { year, month, day } = istParts(date);
  return `${year}-${month}-${day}`;
}

/** Current (or given) time as HH:MM in IST. */
export function nowHHMM(date: Date = new Date()): string {
  const { hour, minute } = istParts(date);
  return `${hour}:${minute}`;
}
