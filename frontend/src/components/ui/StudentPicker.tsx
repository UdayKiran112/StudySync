import { useState, useRef, useEffect } from "react";
import { Search, X } from "lucide-react";
import { useStudentSearch } from "../../api/students";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import type { Student } from "../../api/types";
import { IdTab } from "./Tabs";

export function StudentPicker({
  value,
  onChange,
  activeOnly = true,
}: {
  value: Student | null;
  onChange: (student: Student | null) => void;
  activeOnly?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const debounced = useDebouncedValue(query, 250);
  const containerRef = useRef<HTMLDivElement>(null);

  const { data } = useStudentSearch({
    search: debounced || undefined,
    status: activeOnly ? "Active" : undefined,
    limit: 8,
  });

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  if (value) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border bg-paper-dim px-3 py-2">
        <IdTab>{value.student_id}</IdTab>
        <span className="flex-1 text-sm font-medium text-ink">{value.name}</span>
        <button
          type="button"
          onClick={() => onChange(null)}
          className="rounded p-1 text-slate hover:bg-white hover:text-ink"
          aria-label="Clear selected student"
        >
          <X size={14} />
        </button>
      </div>
    );
  }

  return (
    <div className="relative" ref={containerRef}>
      <div className="relative">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-light" />
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder="Search by name or student ID…"
          className="w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 text-sm text-ink placeholder:text-slate-light focus:border-brass focus:outline-none"
        />
      </div>
      {open && data && data.length > 0 && (
        <div className="absolute z-10 mt-1 w-full overflow-hidden rounded-md border border-border bg-card shadow-lg">
          {data.map((s) => (
            <button
              type="button"
              key={s.student_id}
              onClick={() => {
                onChange(s);
                setQuery("");
                setOpen(false);
              }}
              className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-paper-dim"
            >
              <IdTab>{s.student_id}</IdTab>
              <span>{s.name}</span>
            </button>
          ))}
        </div>
      )}
      {open && query && data && data.length === 0 && (
        <div className="absolute z-10 mt-1 w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-slate shadow-lg">
          No matching students
        </div>
      )}
    </div>
  );
}
