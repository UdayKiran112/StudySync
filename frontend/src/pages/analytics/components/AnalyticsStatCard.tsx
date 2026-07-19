import type { ReactNode } from "react";

export function AnalyticsStatCard({
  label,
  value,
  sub,
  icon: Icon,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}) {
  return (
    <div className="print-break-avoid rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2 text-slate">
        <Icon size={15} className="text-brass" />
        <p className="text-xs font-medium uppercase tracking-wide">{label}</p>
      </div>
      <p className="mt-2 font-display text-2xl font-semibold text-ink">{value}</p>
      {sub && <div className="mt-1">{sub}</div>}
    </div>
  );
}
