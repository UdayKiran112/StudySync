import clsx from "clsx";
import { formatClockTime, formatDuration } from "../../lib/format";
import { useLiveNow } from "../../lib/useLiveNow";

/** Ticking clock for a page header. Isolates the 1s re-render to itself. */
export function LiveClock({ size = "lg" }: { size?: "lg" | "sm" }) {
  const now = useLiveNow();
  return (
    <div className="text-right">
      <p className="text-xs uppercase tracking-[0.32em] text-slate-light">
        Current time
      </p>
      <p
        className={clsx(
          "mt-1 font-display font-semibold tabular-nums text-ink",
          size === "lg" ? "text-2xl" : "text-lg",
        )}
      >
        {formatClockTime(now)}
      </p>
      <p className="mt-0.5 text-sm text-slate">
        {now.toLocaleDateString([], {
          weekday: "long",
          month: "long",
          day: "numeric",
        })}
      </p>
    </div>
  );
}

/** Live elapsed duration for a still-open session. */
export function OpenSessionDuration({ checkInDate }: { checkInDate: Date }) {
  const now = useLiveNow();
  const minutes = Math.max(
    0,
    Math.round((now.getTime() - checkInDate.getTime()) / 60000),
  );
  return <span className="text-slate">{formatDuration(minutes)}</span>;
}
