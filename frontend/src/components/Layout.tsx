import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
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
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useSettings } from "../context/SettingsContext";
import clsx from "clsx";

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
    items: [{ to: "/coaching-classes", icon: Presentation, label: "Coaching classes" }],
  },
  {
    label: "Activities",
    items: [{ to: "/other-activities", icon: Mic, label: "Other activities" }],
  },
  {
    label: "Reports",
    items: [{ to: "/analytics", icon: BarChart3, label: "Student analytics" }],
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
  const { isConfigured } = useSettings();
  const [open, setOpen] = useState(getInitialOpen);

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
              <p className="mt-1 text-[11px] uppercase tracking-widest text-paper/50">
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
          {NAV_SECTIONS.map((section) => (
            <div key={section.label} className="mb-5">
              <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-widest text-paper/40">
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
        <div className="mx-auto max-w-6xl px-4 py-6 print-area sm:px-6 sm:py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
