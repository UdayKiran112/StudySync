import { TrendingUp, TrendingDown, Minus } from "lucide-react";

function classify(trend: string): "up" | "down" | "flat" {
  const t = trend.toLowerCase();
  if (/\b(up|improv|increas|better|rising)\b/.test(t)) return "up";
  if (/\b(down|declin|decreas|worse|falling|drop)\b/.test(t)) return "down";
  return "flat";
}

export function TrendBadge({
  trend,
  delta,
  suffix = "pts",
}: {
  trend: string;
  delta?: number | null;
  suffix?: string;
}) {
  const kind = classify(trend);
  const Icon = kind === "up" ? TrendingUp : kind === "down" ? TrendingDown : Minus;
  const colorClass = kind === "up" ? "text-forest" : kind === "down" ? "text-rust" : "text-slate";

  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${colorClass}`}>
      <Icon size={13} />
      {trend}
      {delta !== null && delta !== undefined && (
        <span className="text-slate-light">
          ({delta > 0 ? "+" : ""}
          {delta.toFixed(1)} {suffix})
        </span>
      )}
    </span>
  );
}
