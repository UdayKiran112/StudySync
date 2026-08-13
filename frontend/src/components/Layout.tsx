import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import toast, { type Toast } from "react-hot-toast";
import {
  LayoutDashboard,
  Users,
  ClipboardCheck,
  Laptop,
  BookOpen,
  Library,
  Layers,
  GraduationCap,
  ListChecks,
  Presentation,
  Mic,
  Settings as SettingsIcon,
  CircleAlert,
  BarChart3,
  CalendarDays,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Search as SearchIcon,
  LogIn,
  LogOut,
} from "lucide-react";
import { useSettings } from "../context/SettingsContext";
import {
  useRealtimeEvents,
  type AttendanceEvent,
  type RenewalEvent,
} from "../api/realtime";
import { formatDate } from "../lib/format";
import { CommandPalette } from "./ui/CommandPalette";
import clsx from "clsx";

/** Renewal notifications outlive normal toasts so the desk can't miss them. */
const RENEWAL_TOAST_MS = 10000;
/** Live punch notifications: long enough to read, short enough to not pile up. */
const PUNCH_TOAST_MS = 5000;

/** A compact live-punch notification: who swiped, which direction, at what time. */
function PunchToast({
  t,
  name,
  direction,
  time,
}: {
  t: Toast;
  name: string;
  direction: "in" | "out";
  time: string;
}) {
  const Icon = direction === "in" ? LogIn : LogOut;
  return (
    <div
      className={clsx(
        "pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border bg-card p-4 shadow-lg",
        direction === "in" ? "border-brass/40" : "border-slate/30",
      )}
      onClick={() => toast.dismiss(t.id)}
    >
      <span
        className={clsx(
          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
          direction === "in"
            ? "bg-brass/15 text-brass"
            : "bg-slate/10 text-slate",
        )}
      >
        <Icon size={16} />
      </span>
      <div className="flex-1">
        <p className="font-display text-sm font-semibold text-ink">{name}</p>
        <p className="mt-0.5 text-xs text-slate">
          {direction === "in" ? "Punched in at" : "Punched out at"} {time}
        </p>
      </div>
    </div>
  );
}

const NAV_SECTIONS = [
  {
    label: "Overview",
    items: [{ to: "/", icon: LayoutDashboard, label: "Dashboard", end: true }],
  },
  {
    label: "Front desk",
    items: [
      { to: "/students", icon: Users, label: "Students" },
      { to: "/attendance", icon: ClipboardCheck, label: "Attendance" },
    ],
  },
  {
    label: "Library",
    items: [
      { to: "/digital-library", icon: Laptop, label: "Digital library" },
      { to: "/offline-library", icon: BookOpen, label: "Offline library" },
      { to: "/books", icon: Library, label: "Books catalog" },
      { to: "/subscriptions", icon: Layers, label: "Subscriptions" },
    ],
  },
  {
    label: "Assessments",
    items: [
      { to: "/exams", icon: GraduationCap, label: "Exams" },
      { to: "/quizzes", icon: ListChecks, label: "Quizzes" },
    ],
  },
  {
    label: "Coaching",
    items: [
      {
        to: "/coaching-classes",
        icon: Presentation,
        label: "Coaching classes",
      },
    ],
  },
  {
    label: "Activities",
    items: [{ to: "/other-activities", icon: Mic, label: "Other activities" }],
  },
  {
    label: "Reports",
    items: [
      { to: "/analytics", icon: BarChart3, label: "Student analytics" },
      { to: "/holidays", icon: CalendarDays, label: "Holidays" },
    ],
  },
];

const DESKTOP_BREAKPOINT = "(min-width: 1024px)";
const SIDEBAR_PREF_KEY = "studysync.sidebarOpen";

/** Sidebar starts open on desktop (honoring any saved preference) and closed on mobile. */
function getInitialOpen(): boolean {
  if (typeof window === "undefined") return true;
  if (!window.matchMedia(DESKTOP_BREAKPOINT).matches) return false;
  const stored = localStorage.getItem(SIDEBAR_PREF_KEY);
  return stored === null ? true : stored === "true";
}

export function Layout() {
  const { isConfigured, clearSettings } = useSettings();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(getInitialOpen);
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Ctrl/Cmd+K and "/" open the quick-find palette from anywhere. "/" is
  // skipped while typing so it doesn't hijack form fields.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const typing =
        e.target instanceof HTMLElement &&
        (e.target.tagName === "INPUT" ||
          e.target.tagName === "TEXTAREA" ||
          e.target.tagName === "SELECT" ||
          e.target.isContentEditable);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      if (e.key === "/" && !typing) {
        e.preventDefault();
        setPaletteOpen(true);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Cross-page navigation should start from the top, not from wherever the
  // previous page was scrolled (pagination clicks stay put — they don't
  // change the route).
  const { pathname } = location;
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  // Live stream: punches written by the ZKTeco poller/live transport
  // instantly invalidate the attendance queries (so rows appear with no
  // polling delay) and pop a live notification when someone swipes in or
  // out. Auto-renewals surface as a notification instead of a modal
  // prompt. A burst of renewals (e.g. a full device re-import) coalesces
  // onto one toast so the desk isn't spammed -- subsequent renewals within
  // the window fold into the same toast.
  const renewalToastRef = useRef<{ id: string; count: number } | null>(null);
  useRealtimeEvents({
    onAttendance: (event: AttendanceEvent) => {
      // Only a punch happening TODAY is a live swipe at the desk. A past
      // day's session completed by a later re-read is historical backfill,
      // not someone walking in right now -- don't notify for it.
      const now = new Date();
      const today = [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, "0"),
        String(now.getDate()).padStart(2, "0"),
      ].join("-");
      if (event.day !== today) return;
      const name = event.name ?? `Student #${event.student_id}`;
      toast.custom(
        (t) => (
          <PunchToast
            t={t}
            name={name}
            direction={event.outcome === "checked_out" ? "out" : "in"}
            time={event.punch}
          />
        ),
        { duration: PUNCH_TOAST_MS },
      );
    },
    onRenewal: (event: RenewalEvent) => {
      const name = event.name ?? `Student #${event.student_id}`;
      const message = event.valid_until
        ? `${name} — membership renewed, valid until ${formatDate(event.valid_until)}`
        : `${name} — membership renewed`;
      const active = renewalToastRef.current;
      if (active) {
        active.count += 1;
        toast.success(`${message} · ${active.count} more`, {
          id: active.id,
          duration: RENEWAL_TOAST_MS,
        });
        return;
      }
      const id = toast.success(message, { duration: RENEWAL_TOAST_MS });
      renewalToastRef.current = { id, count: 0 };
      window.setTimeout(() => {
        if (renewalToastRef.current?.id === id) renewalToastRef.current = null;
      }, RENEWAL_TOAST_MS);
    },
  });

  // Remember the toggle choice, but only for desktop — mobile always starts closed.
  useEffect(() => {
    if (window.matchMedia(DESKTOP_BREAKPOINT).matches) {
      localStorage.setItem(SIDEBAR_PREF_KEY, String(open));
    }
  }, [open]);

  // Crossing the desktop/mobile breakpoint (e.g. rotating a tablet, resizing a
  // window) should re-apply the right default rather than leaving a mobile
  // drawer stuck open behind desktop content or vice versa.
  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_BREAKPOINT);
    const handle = (e: MediaQueryListEvent) => {
      if (e.matches) {
        const stored = localStorage.getItem(SIDEBAR_PREF_KEY);
        setOpen(stored === null ? true : stored === "true");
      } else {
        setOpen(false);
      }
    };
    mq.addEventListener("change", handle);
    return () => mq.removeEventListener("change", handle);
  }, []);

  function closeOnMobile() {
    if (!window.matchMedia(DESKTOP_BREAKPOINT).matches) setOpen(false);
  }

  // Sign out: wipe the stored staff API key (and settings) from this browser
  // so nobody inheriting the machine's browser profile inherits the key, then
  // land on Settings so the staff member can re-enter it.
  function handleSignOut() {
    clearSettings();
    toast.success("Signed out — stored key removed from this browser.");
    navigate("/settings");
  }

  return (
    <div className="min-h-screen bg-paper">
      {/* Mobile top bar — hidden on desktop */}
      <div className="no-print sticky top-0 z-30 flex items-center gap-3 border-b border-border bg-card px-4 py-3 lg:hidden">
        <button
          onClick={() => setOpen(true)}
          className="rounded-md p-1.5 text-ink hover:bg-paper-dim"
          aria-label="Open menu"
        >
          <Menu size={20} />
        </button>
        <div className="tab-clip flex h-7 w-7 items-center justify-center bg-brass text-ink font-display text-xs font-bold">
          S
        </div>
        <p className="font-display text-sm font-semibold text-ink">StudySync</p>
        <button
          onClick={() => setPaletteOpen(true)}
          className="ml-auto rounded-md p-1.5 text-ink hover:bg-paper-dim"
          aria-label="Quick find"
          title="Quick find (Ctrl+K)"
        >
          <SearchIcon size={18} />
        </button>
      </div>

      {/* Backdrop — mobile only, shown while the drawer is open */}
      {open && (
        <div
          className="no-print fixed inset-0 z-40 bg-ink/40 lg:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar — off-canvas drawer on mobile, collapsible panel on desktop */}
      <aside
        className={clsx(
          "fixed inset-y-0 left-0 z-50 flex w-64 flex-col bg-ink text-paper transition-transform duration-200 ease-in-out",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex items-center justify-between gap-2.5 border-b border-white/10 px-5 py-5">
          <div className="flex items-center gap-2.5">
            <div className="tab-clip flex h-8 w-8 items-center justify-center bg-brass text-ink font-display text-sm font-bold">
              S
            </div>
            <div>
              <p className="font-display text-base font-semibold leading-none">
                StudySync
              </p>
              <p className="mt-1 text-xs uppercase tracking-widest text-paper/70">
                Front desk
              </p>
            </div>
          </div>
          <button
            onClick={() => setOpen(false)}
            className="rounded p-1.5 text-paper/60 hover:bg-white/10 hover:text-white"
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
          >
            <PanelLeftClose size={17} />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto scrollbar-thin px-3 py-4">
          <button
            onClick={() => setPaletteOpen(true)}
            className="mb-5 flex w-full items-center gap-2.5 rounded-md border border-white/10 bg-white/5 px-3 py-2 text-sm text-paper/70 transition-colors hover:bg-white/10 hover:text-white"
            title="Quick find (Ctrl+K)"
          >
            <SearchIcon size={15} />
            <span className="flex-1 text-left">Quick find…</span>
            <kbd className="rounded border border-white/20 px-1.5 py-0.5 font-mono text-[10px]">
              Ctrl K
            </kbd>
          </button>
          {NAV_SECTIONS.map((section) => (
            <div key={section.label} className="mb-5">
              <p className="mb-1.5 px-3 text-xs font-semibold uppercase tracking-widest text-paper/60">
                {section.label}
              </p>
              <div className="space-y-0.5">
                {section.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={"end" in item ? item.end : false}
                    onClick={closeOnMobile}
                    className={({ isActive }) =>
                      clsx(
                        "flex items-center gap-2.5 rounded-md border-l-2 px-3 py-2 text-sm transition-colors",
                        isActive
                          ? "border-brass bg-white/[0.07] font-medium text-white"
                          : "border-transparent text-paper/70 hover:bg-white/5 hover:text-white",
                      )
                    }
                  >
                    <item.icon size={16} />
                    {item.label}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="border-t border-white/10 px-3 py-3">
          <NavLink
            to="/settings"
            onClick={closeOnMobile}
            className={({ isActive }) =>
              clsx(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-white/10 text-white"
                  : "text-paper/70 hover:bg-white/5 hover:text-white",
              )
            }
          >
            <SettingsIcon size={16} />
            Settings
            {!isConfigured && (
              <CircleAlert size={14} className="ml-auto text-brass-light" />
            )}
          </NavLink>
          <button
            onClick={handleSignOut}
            className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm text-paper/70 transition-colors hover:bg-white/5 hover:text-white"
            title="Remove the stored staff API key from this browser"
          >
            <LogOut size={16} />
            Sign out
          </button>
        </div>
      </aside>

      {/* Reopen button — desktop only, shown once the panel is collapsed */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="no-print fixed left-4 top-4 z-30 hidden items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-2 text-ink shadow-sm hover:bg-paper-dim lg:flex"
          aria-label="Open sidebar"
          title="Open sidebar"
        >
          <PanelLeftOpen size={17} />
        </button>
      )}

      {/* Main content — shifts right on desktop while the sidebar is open; on
          mobile the sidebar is an overlay, so content always stays full-width */}
      <main
        className={clsx(
          "min-h-screen transition-[margin] duration-200 ease-in-out",
          open && "lg:ml-64",
        )}
      >
        {!isConfigured && (
          <div className="no-print flex items-center gap-2 border-b border-brass/30 bg-brass/10 px-4 py-2.5 text-sm text-brass sm:px-6">
            <CircleAlert size={15} className="shrink-0" />
            <span>
              Set the API base URL and staff key in{" "}
              <NavLink
                to="/settings"
                className="font-medium underline underline-offset-2"
              >
                Settings
              </NavLink>{" "}
              before using StudySync.
            </span>
          </div>
        )}
        <div
          key={location.pathname}
          className="anim-fade-up mx-auto max-w-6xl px-4 py-6 print-area sm:px-6 sm:py-8"
        >
          <Outlet />
        </div>
      </main>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
