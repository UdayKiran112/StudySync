import type { CSSProperties, ReactNode } from "react";
import {
  Loader2,
  Inbox,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { Button } from "./Button";

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate">
      <Loader2 size={18} className="animate-spin" />
      {label}
    </div>
  );
}

/** A shimmering placeholder bar. Compose these to build skeletons that
 *  roughly match the shape of the content they'll be replaced by, so the
 *  layout doesn't jump when real data arrives. */
export function Skeleton({
  className,
  style,
}: {
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div
      className={`skeleton-shimmer rounded ${className ?? "h-4 w-full"}`}
      style={style}
    />
  );
}

/** Drop-in replacement for a table's <tbody> while its data is loading. */
export function TableSkeletonRows({
  rows = 5,
  columns = 4,
}: {
  rows?: number;
  columns?: number;
}) {
  return (
    <tbody>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="border-b border-border last:border-0">
          {Array.from({ length: columns }).map((_, c) => (
            <td key={c} className="px-4 py-3">
              <Skeleton
                className="h-4"
                style={{ width: c === 0 ? "60%" : "80%" }}
              />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

/** Matches Dashboard-style stat/quick-action cards while their data loads. */
export function CardSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={`rounded-lg border border-border bg-card p-5 ${className ?? ""}`}
    >
      <Skeleton className="h-4 w-24" />
      <Skeleton className="mt-3 h-8 w-16" />
      <Skeleton className="mt-2 h-3 w-32" />
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-rust/30 bg-rust-bg px-4 py-3 text-sm text-rust">
      <AlertTriangle size={16} className="mt-0.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border py-16 text-center">
      <Inbox size={28} className="text-slate-light" />
      <div>
        <p className="font-display text-base font-medium text-ink">{title}</p>
        {description && (
          <p className="mt-1 text-sm text-slate">{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        {eyebrow && (
          <p className="mb-1 text-xs font-semibold uppercase tracking-widest text-brass">
            {eyebrow}
          </p>
        )}
        <h1 className="font-display text-2xl font-semibold text-ink">
          {title}
        </h1>
        {description && (
          <p className="mt-1 text-sm text-slate">{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}

export function Pagination({
  offset,
  limit,
  count,
  total,
  onOffsetChange,
}: {
  offset: number;
  limit: number;
  count: number;
  /** Pass this when the full result set is already known client-side
   *  (e.g. an endpoint with no server-side pagination) for an exact
   *  hasNext/hasPrev instead of the count===limit heuristic below. */
  total?: number;
  onOffsetChange: (offset: number) => void;
}) {
  const hasPrev = offset > 0;
  const hasNext =
    total !== undefined ? offset + limit < total : count === limit;
  const shownTotal = total ?? offset + count;
  return (
    <div className="mt-4 flex items-center justify-between text-sm text-slate">
      <span>
        Showing {count === 0 ? 0 : offset + 1}–{offset + count}
        {total !== undefined && ` of ${shownTotal}`}
      </span>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="secondary"
          disabled={!hasPrev}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
        >
          <ChevronLeft size={14} /> Prev
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={!hasNext}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Next <ChevronRight size={14} />
        </Button>
      </div>
    </div>
  );
}
