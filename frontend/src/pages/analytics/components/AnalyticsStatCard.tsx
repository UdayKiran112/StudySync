import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function AnalyticsStatCard({
  label,
  value,
  sub,
  icon: Icon,
  to,
  className,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  to?: string;
  className?: string;
}) {
  const content = (
    <div className={`print-break-avoid rounded-lg border border-border bg-card p-4 ${className ?? ""}`}>
      <div className="flex items-center gap-2 text-slate">
        <Icon size={15} className="text-brass" />
        <p className="text-xs font-medium uppercase tracking-wide">{label}</p>
      </div>
      <p className="mt-2 font-display text-2xl font-semibold text-ink">{value}</p>
      {sub && <div className="mt-1">{sub}</div>}
    </div>
  );
  return to ? <Link to={to} className="block transition-transform hover:-translate-y-0.5">{content}</Link> : content;
}
