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
  Settings as SettingsIcon,
  CircleAlert,
  BarChart3,
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
    label: "Reports",
    items: [{ to: "/analytics", icon: BarChart3, label: "Student analytics" }],
  },
];

export function Layout() {
  const { isConfigured } = useSettings();

  return (
    <div className="flex min-h-screen bg-paper">
      <aside className="flex w-64 shrink-0 flex-col bg-ink text-paper">
        <div className="flex items-center gap-2.5 border-b border-white/10 px-5 py-5">
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

      <main className="flex-1 overflow-y-auto">
        {!isConfigured && (
          <div className="no-print flex items-center gap-2 border-b border-brass/30 bg-brass/10 px-6 py-2.5 text-sm text-brass">
            <CircleAlert size={15} />
            Set the API base URL and staff key in{" "}
            <NavLink
              to="/settings"
              className="font-medium underline underline-offset-2"
            >
              Settings
            </NavLink>{" "}
            before using StudySync.
          </div>
        )}
        <div className="mx-auto max-w-6xl px-6 py-8 print-area">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
