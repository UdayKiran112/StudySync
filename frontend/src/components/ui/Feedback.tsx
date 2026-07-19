import type { ReactNode } from "react";
import { Loader2, Inbox, AlertTriangle, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./Button";

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate">
      <Loader2 size={18} className="animate-spin" />
      {label}
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

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border py-16 text-center">
      <Inbox size={28} className="text-slate-light" />
      <div>
        <p className="font-display text-base font-medium text-ink">{title}</p>
        {description && <p className="mt-1 text-sm text-slate">{description}</p>}
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
        {eyebrow && <p className="mb-1 text-xs font-semibold uppercase tracking-widest text-brass">{eyebrow}</p>}
        <h1 className="font-display text-2xl font-semibold text-ink">{title}</h1>
        {description && <p className="mt-1 text-sm text-slate">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function Pagination({
  offset,
  limit,
  count,
  onOffsetChange,
}: {
  offset: number;
  limit: number;
  count: number;
  onOffsetChange: (offset: number) => void;
}) {
  const hasPrev = offset > 0;
  const hasNext = count === limit;
  return (
    <div className="mt-4 flex items-center justify-between text-sm text-slate">
      <span>
        Showing {count === 0 ? 0 : offset + 1}–{offset + count}
      </span>
      <div className="flex gap-2">
        <Button size="sm" variant="secondary" disabled={!hasPrev} onClick={() => onOffsetChange(Math.max(0, offset - limit))}>
          <ChevronLeft size={14} /> Prev
        </Button>
        <Button size="sm" variant="secondary" disabled={!hasNext} onClick={() => onOffsetChange(offset + limit)}>
          Next <ChevronRight size={14} />
        </Button>
      </div>
    </div>
  );
}
