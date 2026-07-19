import clsx from "clsx";
import type { ReactNode } from "react";

/**
 * The app's signature mark: a clipped-corner "index card tab" — the same
 * shape a library card-catalog drawer label uses. Used consistently for
 * record IDs (mono, ink-on-paper) and status flags (colored) so every
 * list in the app reads like a row of catalog cards.
 */
export function IdTab({ children }: { children: ReactNode }) {
  return (
    <span className="tab-clip inline-flex items-center bg-paper-dim px-2.5 py-1 font-mono text-xs font-medium text-ink">
      {children}
    </span>
  );
}

type StatusTone = "forest" | "rust" | "brass" | "slate";

const toneClasses: Record<StatusTone, string> = {
  forest: "bg-forest-bg text-forest",
  rust: "bg-rust-bg text-rust",
  brass: "bg-brass/15 text-brass",
  slate: "bg-paper-dim text-slate",
};

export function StatusTab({ tone, children }: { tone: StatusTone; children: ReactNode }) {
  return (
    <span
      className={clsx(
        "tab-clip inline-flex items-center px-2.5 py-1 text-xs font-semibold uppercase tracking-wide",
        toneClasses[tone]
      )}
    >
      {children}
    </span>
  );
}

export function studentStatusTone(status: string): StatusTone {
  return status === "Active" ? "forest" : "slate";
}

export function subscriptionStatusTone(status: string): StatusTone {
  return status === "Active" ? "forest" : "rust";
}
