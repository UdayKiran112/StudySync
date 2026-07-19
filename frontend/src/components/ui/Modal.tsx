import { type ReactNode, useEffect } from "react";
import { X } from "lucide-react";

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  width = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: ReactNode;
  width?: "sm" | "md" | "lg";
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const widthClass = { sm: "max-w-sm", md: "max-w-lg", lg: "max-w-2xl" }[width];

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/40 px-4 py-8 backdrop-blur-[1px]">
      <div
        className={`w-full ${widthClass} rounded-lg border border-border bg-card shadow-xl`}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-start justify-between border-b border-border px-5 py-4">
          <div>
            <h2 className="font-display text-lg font-medium text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-slate">{subtitle}</p>}
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-slate hover:bg-paper-dim hover:text-ink"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
