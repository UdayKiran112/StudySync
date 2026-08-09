export interface CsvColumn {
  key: string;
  label: string;
}

/** Builds a CSV string from row objects and triggers a browser download. */
export function downloadCsv(
  filename: string,
  rows: Record<string, string | number | null | undefined>[],
  columns?: CsvColumn[],
) {
  if (rows.length === 0) return;

  const escapeCell = (value: string | number | null | undefined) => {
    const s = value === null || value === undefined ? "" : String(value);
    if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  };

  const headers = columns ? columns.map((c) => c.label) : Object.keys(rows[0]);
  const keys = columns ? columns.map((c) => c.key) : headers;

  const lines = [
    headers.map(escapeCell).join(","),
    ...rows.map((row) => keys.map((key) => escapeCell(row[key])).join(",")),
  ];

  // UTF-8 BOM so Excel detects the encoding and shows non-ASCII text correctly.
  const blob = new Blob(["\uFEFF", lines.join("\n")], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
