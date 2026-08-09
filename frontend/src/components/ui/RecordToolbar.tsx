import type { ReactNode } from "react";

/**
 * A neat, boxed toolbar for record/report pages: a title + summary on the
 * left, with filter controls and actions (e.g. the export menu) aligned on
 * the right. Gives every report page one consistent place for its filters
 * and export options instead of loose floating controls.
 */
export function RecordToolbar({
  title,
  description,
  controls,
  actions,
}: {
  title: string;
  description?: ReactNode;
  controls?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 rounded-2xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="min-w-48">
          <p className="font-medium text-ink">{title}</p>
          {description && (
            <p className="mt-1 text-sm text-slate">{description}</p>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          {controls}
          {actions}
        </div>
      </div>
    </div>
  );
}
