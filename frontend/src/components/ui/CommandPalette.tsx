import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, CornerDownLeft, ArrowUpRight } from "lucide-react";
import { useStudentSearch } from "../../api/students";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { IdTab } from "./Tabs";

const NAV_ACTIONS = [
  { to: "/attendance", label: "Check a student in or out" },
  { to: "/digital-library", label: "Start a digital library session" },
  { to: "/offline-library", label: "Log an offline library visit" },
  { to: "/students", label: "Browse the student list" },
  { to: "/exams", label: "Browse exams" },
  { to: "/quizzes", label: "Browse quizzes" },
  { to: "/coaching-classes", label: "Open coaching classes" },
  { to: "/other-activities", label: "Open other activities" },
  { to: "/analytics", label: "Open student analytics" },
];

interface NavAction {
  to: string;
  label: string;
}
type PaletteItem =
  | { kind: "student"; student: import("../../api/types").Student }
  | { kind: "action"; action: NavAction };

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounced = useDebouncedValue(query, 300);

  const canSearch = debounced.trim().length >= 2;
  const { data: results } = useStudentSearch(
    { search: canSearch ? debounced.trim() : undefined, limit: 8 },
    canSearch,
  );

  const navActions = useMemo(
    () =>
      NAV_ACTIONS.filter((a) =>
        a.label.toLowerCase().includes(query.trim().toLowerCase()),
      ),
    [query],
  );

  const items = useMemo<PaletteItem[]>(
    () => [
      ...navActions.map((action): PaletteItem => ({ kind: "action", action })),
      ...(canSearch ? (results ?? []).map((student): PaletteItem => ({ kind: "student", student })) : []),
    ],
    [navActions, canSearch, results],
  );

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      // Focus on the next frame so the input exists before we move focus.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query, results]);

  if (!open) return null;

  function run(item: PaletteItem) {
    if (item.kind === "action") {
      navigate(item.action.to);
    } else {
      navigate(`/students/${item.student.student_id}`);
    }
    onClose();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (items.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(items[activeIndex]);
    }
  }

  return (
    <div
      className="no-print fixed inset-0 z-50 flex items-start justify-center bg-ink/40 p-4 pt-20"
      onMouseDown={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Quick find"
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded-xl border border-border bg-card shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2.5 border-b border-border px-4">
          <Search size={16} className="shrink-0 text-slate-light" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search students, or jump to a page…"
            className="w-full bg-transparent py-3.5 text-sm text-ink placeholder:text-slate-light focus:outline-none"
            aria-label="Search students or pages"
          />
          <kbd className="hidden rounded border border-border bg-paper-dim px-1.5 py-0.5 font-mono text-[10px] text-slate sm:block">
            Esc
          </kbd>
        </div>

        <div className="max-h-80 overflow-y-auto p-2">
          {items.length === 0 && (
            <p className="px-3 py-8 text-center text-sm text-slate">
              {query.trim() ? "No matches found." : "Type to search students or jump to a page."}
            </p>
          )}
          {items.map((item, index) => {
            const active = index === activeIndex;
            return (
              <button
                key={
                  item.kind === "student"
                    ? `student-${item.student.student_id}`
                    : `action-${item.action.to}`
                }
                type="button"
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => run(item)}
                className={`flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm ${
                  active ? "bg-paper-dim" : "hover:bg-paper-dim"
                }`}
              >
                {item.kind === "student" ? (
                  <>
                    <IdTab>{item.student.student_id}</IdTab>
                    <span className="flex-1 truncate font-medium text-ink">
                      {item.student.name}
                    </span>
                    <ArrowUpRight size={14} className="text-slate-light" />
                  </>
                ) : (
                  <>
                    <ArrowUpRight size={14} className="text-brass" />
                    <span className="flex-1 truncate text-ink">
                      {item.action.label}
                    </span>
                  </>
                )}
                {active && (
                  <CornerDownLeft size={13} className="shrink-0 text-slate-light" />
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
