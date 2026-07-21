import { useEffect, useMemo, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { useExternalParticipants } from "../../api/coaching";
import type { ExternalParticipant } from "../../api/types";

const RECENT_KEY = "studysync.recent-external-students";
const MAX_RECENT = 10;

function readRecent(): ExternalParticipant[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
  } catch {
    return [];
  }
}
function saveRecent(student: ExternalParticipant) {
  const recent = [
    student,
    ...readRecent().filter(
      (item) =>
        item.external_participant_id !== student.external_participant_id,
    ),
  ].slice(0, MAX_RECENT);
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent));
}
function Highlight({ text, query }: { text: string; query: string }) {
  const position = text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  if (position < 0 || !query) return <>{text}</>;
  return (
    <>
      {text.slice(0, position)}
      <mark className="rounded bg-brass/25 px-0.5 text-inherit">
        {text.slice(position, position + query.length)}
      </mark>
      {text.slice(position + query.length)}
    </>
  );
}

export function ExternalStudentPicker({
  value,
  onChange,
}: {
  value: ExternalParticipant | null;
  onChange: (student: ExternalParticipant | null) => void;
}) {
  const { data: students = [], isLoading } = useExternalParticipants();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const recent = useMemo(readRecent, [open]);
  const results = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return recent;
    return students.filter((s) =>
      [s.name, s.village, s.phone, String(s.external_participant_id)].some(
        (field) => field?.toLocaleLowerCase().includes(needle),
      ),
    );
  }, [query, recent, students]);
  useEffect(() => {
    setActiveIndex(0);
  }, [query, open]);
  useEffect(() => {
    const outside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      )
        setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, []);
  const select = (student: ExternalParticipant) => {
    saveRecent(student);
    onChange(student);
    setQuery("");
    setOpen(false);
  };
  if (value)
    return (
      <div className="flex items-center gap-2 rounded-md border border-border bg-paper-dim px-3 py-2">
        <span className="flex-1 text-sm font-medium text-ink">
          {value.name}{" "}
          <span className="font-normal text-slate">· {value.village}</span>
        </span>
        <button
          type="button"
          onClick={() => onChange(null)}
          className="rounded p-1 text-slate hover:bg-white hover:text-ink"
          aria-label="Clear selected external student"
        >
          <X size={14} />
        </button>
      </div>
    );
  return (
    <div className="relative" ref={containerRef}>
      <div className="relative">
        <Search
          size={14}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-light"
        />
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setOpen(false);
              return;
            }
            if (!open || !results.length) return;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActiveIndex((i) => Math.min(i + 1, results.length - 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setActiveIndex((i) => Math.max(i - 1, 0));
            }
            if (e.key === "Enter") {
              e.preventDefault();
              select(results[activeIndex]);
            }
          }}
          placeholder="Search by name, village, phone, or ID…"
          className="w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 text-sm text-ink placeholder:text-slate-light focus:border-brass focus:outline-none"
        />
      </div>
      {open && (
        <div className="absolute z-10 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-border bg-card shadow-lg">
          {!query && (
            <p className="px-3 pt-2 text-xs font-semibold uppercase tracking-wider text-slate">
              Recent students
            </p>
          )}
          {isLoading ? (
            <p className="px-3 py-3 text-sm text-slate">Loading students…</p>
          ) : results.length ? (
            results.map((student, index) => (
              <button
                type="button"
                key={student.external_participant_id}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => select(student)}
                className={`flex w-full flex-col px-3 py-2 text-left text-sm ${index === activeIndex ? "bg-paper-dim" : "hover:bg-paper-dim"}`}
              >
                <span className="font-medium">
                  <Highlight text={student.name} query={query} />
                </span>
                <span className="text-xs text-slate">
                  <Highlight
                    text={`${student.village ?? ""} · ${student.phone ?? ""} · #${student.external_participant_id}`}
                    query={query}
                  />
                </span>
              </button>
            ))
          ) : (
            <div className="px-3 py-3 text-sm text-slate">
              <p className="font-medium text-ink">No student found.</p>
              <p className="mt-1 text-xs">
                Try searching by student ID, name, phone number, or village.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
