import type { ReactNode } from "react";

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="overflow-x-auto scrollbar-thin">
        <table className="w-full border-collapse text-sm">{children}</table>
      </div>
    </div>
  );
}

export function Thead({ children }: { children: ReactNode }) {
  return (
    <thead className="border-b border-border bg-paper-dim/60 text-left text-xs font-semibold uppercase tracking-wide text-slate">
      <tr>{children}</tr>
    </thead>
  );
}

export function Th({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <th className={`px-4 py-2.5 font-semibold ${className ?? ""}`}>
      {children}
    </th>
  );
}

export function Tr({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick?: () => void;
}) {
  return (
    <tr
      onClick={onClick}
      className={`border-b border-border last:border-0 ${onClick ? "cursor-pointer hover:bg-paper-dim/50" : ""}`}
    >
      {children}
    </tr>
  );
}

export function Td({
  children,
  className,
  onClick,
  title,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  title?: string;
}) {
  return (
    <td
      className={`px-4 py-2.5 align-middle text-ink ${className ?? ""}`}
      onClick={onClick}
      title={title}
    >
      {children}
    </td>
  );
}
