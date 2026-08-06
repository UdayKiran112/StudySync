import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";
import clsx from "clsx";
import { Input } from "../../../components/ui/Form";
import { Spinner, ErrorBanner, EmptyState } from "../../../components/ui/Feedback";
import { useCoachingClasses } from "../../../api/coaching";
import { extractErrorMessage } from "../../../api/client";
import { formatDate } from "../../../lib/format";
import { useDebouncedValue } from "../../../lib/useDebouncedValue";

export function SessionList({
  selectedId,
  onSelect,
}: {
  selectedId: number | null;
  onSelect: (classId: number) => void;
}) {
  const [search, setSearch] = useState("");
  const [dateFilter, setDateFilter] = useState("");
  const debouncedSearch = useDebouncedValue(search);

  // The backend only filters sessions by exact date (date_) — there's no
  // title/subject/instructor search server-side — so text search is done
  // client-side over whatever the date filter already narrowed down to.
  const { data, isLoading, isError, error } = useCoachingClasses({ date_: dateFilter || undefined });

  const filtered = useMemo(() => {
    if (!data) return data;
    const q = debouncedSearch.trim().toLowerCase();
    if (!q) return data;
    return data.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.subject?.toLowerCase().includes(q) ||
        c.instructor_name?.toLowerCase().includes(q)
    );
  }, [data, debouncedSearch]);

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="space-y-2 border-b border-border p-3">
        <div className="relative">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-light" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search sessions…"
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <Input type="date" value={dateFilter} onChange={(e) => setDateFilter(e.target.value)} className="flex-1" />
          {dateFilter && (
            <button
              onClick={() => setDateFilter("")}
              className="flex shrink-0 items-center gap-1 rounded-full bg-brass/15 px-2.5 py-1.5 text-xs font-medium text-brass hover:bg-brass/25"
              aria-label="Clear date filter"
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      <div className="max-h-[34rem] space-y-1.5 overflow-y-auto p-3 scrollbar-thin">
        {isLoading && <Spinner label="Loading sessions…" />}
        {isError && <ErrorBanner message={extractErrorMessage(error)} />}
        {filtered && filtered.length === 0 && (
          <EmptyState title="No sessions found" description="Try a different search or date, or create one." />
        )}
        {filtered?.map((cls) => (
          <button
            key={cls.class_id}
            onClick={() => onSelect(cls.class_id)}
            className={clsx(
              "w-full rounded-md border px-3 py-2.5 text-left transition-colors",
              selectedId === cls.class_id ? "border-brass bg-brass/10" : "border-transparent bg-paper-dim/50 hover:bg-paper-dim"
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <p className="truncate text-sm font-medium text-ink">{cls.title}</p>
              <span className="shrink-0 text-xs text-slate-light">{formatDate(cls.class_date)}</span>
            </div>
            <div className="mt-1 flex items-center gap-1.5 text-xs text-slate">
              {cls.subject && <span>{cls.subject}</span>}
              {cls.subject && cls.instructor_name && <span>·</span>}
              {cls.instructor_name && <span>{cls.instructor_name}</span>}
              {!cls.subject && !cls.instructor_name && <span className="text-slate-light">No subject or instructor set</span>}
            </div>
            {(cls.start_time || cls.end_time) && (
              <div className="mt-1 font-mono text-xs text-slate-light">
                {cls.start_time ?? "—"}–{cls.end_time ?? "—"}
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
