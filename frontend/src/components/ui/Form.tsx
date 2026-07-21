import { forwardRef, type InputHTMLAttributes, type KeyboardEvent, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import clsx from "clsx";

const baseFieldClasses =
  "w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-ink placeholder:text-slate-light focus:border-brass focus:outline-none disabled:bg-paper-dim disabled:text-slate-light";

/** Submits the nearest form through its normal validation and submit handler. */
function submitOnEnter(event: KeyboardEvent<HTMLElement>) {
  if (event.defaultPrevented || event.key !== "Enter" || event.nativeEvent.isComposing) return;
  const form = event.currentTarget.closest("form");
  if (!form) return;
  event.preventDefault();
  form.requestSubmit();
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, onKeyDown, ...props }, ref) => (
    <input
      ref={ref}
      className={clsx(baseFieldClasses, className)}
      onKeyDown={(event) => {
        onKeyDown?.(event);
        submitOnEnter(event);
      }}
      {...props}
    />
  )
);
Input.displayName = "Input";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, onKeyDown, ...props }, ref) => (
    <select
      ref={ref}
      className={clsx(baseFieldClasses, className)}
      onKeyDown={(event) => {
        onKeyDown?.(event);
        submitOnEnter(event);
      }}
      {...props}
    >
      {children}
    </select>
  )
);
Select.displayName = "Select";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref} className={clsx(baseFieldClasses, "resize-none", className)} {...props} />
  )
);
Textarea.displayName = "Textarea";

export function Field({
  label,
  required,
  error,
  children,
  hint,
}: {
  label: string;
  required?: boolean;
  error?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-baseline gap-1 text-xs font-medium uppercase tracking-wide text-slate">
        {label}
        {required && <span className="text-rust">*</span>}
      </span>
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-slate-light">{hint}</span>}
      {error && <span className="mt-1 block text-xs text-rust">{error}</span>}
    </label>
  );
}
