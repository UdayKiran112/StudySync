import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import toast from "react-hot-toast";
import { Download, Printer, ChevronDown, FileSpreadsheet } from "lucide-react";
import { Button } from "./Button";
import { downloadCsv } from "../../lib/csvExport";
import { downloadExcel } from "../../lib/excelExport";
import type { ExcelColumn } from "../../lib/excelExport";

export type ExportRow = Record<string, string | number | null | undefined>;
export type ExportColumn = ExcelColumn;

const PRINT_STYLES = `
.print-only { display: none; }
@media print {
  body *:not(.print-only):not(.print-only *) { visibility: hidden !important; }
  .print-only { display: block; visibility: visible; position: absolute; left: 0; top: 0; width: 100%; padding: 12px 16px; }
  .print-only h2 { font-family: Georgia, serif; font-size: 16pt; font-weight: 700; margin: 0 0 10px; color: #000; }
  .print-only table { width: 100%; border-collapse: collapse; font-size: 9pt; color: #000; }
  .print-only th, .print-only td { border: 1px solid #000; padding: 4px 6px; text-align: left; vertical-align: top; word-break: break-word; }
  .print-only th { background: #e8e8e8; font-weight: 700; }
  .print-only tr { break-inside: avoid; }
}
`;

export function ExportMenu({
  title,
  filename,
  columns,
  getRows,
}: {
  /** Heading shown at the top of the generated PDF. */
  title: string;
  /** Base filename (no extension) used for CSV / Excel downloads. */
  filename: string;
  /** Column definitions — also control the header order for CSV/Excel/PDF. */
  columns: ExportColumn[];
  /**
   * Returns the complete (unpaginated) set of rows to export. May be
   * async when the full dataset has to be fetched on demand.
   */
  getRows: () => ExportRow[] | Promise<ExportRow[]>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"csv" | "excel" | "pdf" | null>(null);
  const [printRows, setPrintRows] = useState<ExportRow[] | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  // Clear the hidden print table once the print job finishes, so a stale
  // snapshot never lingers (it's display:none on screen either way).
  useEffect(() => {
    if (!printRows) return;
    const afterPrint = () => setPrintRows(null);
    window.addEventListener("afterprint", afterPrint);
    return () => window.removeEventListener("afterprint", afterPrint);
  }, [printRows]);

  async function loadRows() {
    const rows = await getRows();
    if (rows.length === 0) {
      toast.error("No records to export");
      return null;
    }
    return rows;
  }

  async function exportData(format: "csv" | "excel") {
    setOpen(false);
    if (busy) return;
    setBusy(format);
    try {
      const rows = await loadRows();
      if (!rows) return;
      if (format === "csv") downloadCsv(filename, rows, columns);
      else downloadExcel(filename, columns, rows);
      toast.success(
        `Exported ${rows.length} record${rows.length === 1 ? "" : "s"}`,
      );
    } catch {
      toast.error("Export failed. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  async function printPdf() {
    setOpen(false);
    if (busy) return;
    setBusy("pdf");
    try {
      const rows = await loadRows();
      if (!rows) return;
      // Render the hidden print-only report synchronously so the browser's
      // print preview shows the freshly-built report, not a stale one.
      flushSync(() => setPrintRows(rows));
      window.print();
    } catch {
      toast.error("Print failed. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="relative no-print" ref={ref}>
        <Button
          variant="secondary"
          size="sm"
          disabled={busy !== null}
          onClick={() => setOpen((v) => !v)}
        >
          <Download size={15} /> {busy ? "Preparing…" : "Export"}{" "}
          <ChevronDown size={13} />
        </Button>
        {open && (
          <div className="absolute right-0 z-10 mt-1 w-72 overflow-hidden rounded-md border border-border bg-card shadow-lg">
            <button
              onClick={printPdf}
              className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm hover:bg-paper-dim"
            >
              <Printer size={15} className="text-brass" />
              <div>
                <p className="font-medium text-ink">Print / Save as PDF</p>
                <p className="text-xs text-slate-light">All matching records</p>
              </div>
            </button>
            <div className="border-t border-border" />
            <button
              onClick={() => exportData("csv")}
              className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm hover:bg-paper-dim"
            >
              <FileSpreadsheet size={15} className="text-brass" />
              <div>
                <p className="font-medium text-ink">Download CSV</p>
                <p className="text-xs text-slate-light">
                  Open in any spreadsheet app
                </p>
              </div>
            </button>
            <button
              onClick={() => exportData("excel")}
              className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm hover:bg-paper-dim"
            >
              <FileSpreadsheet size={15} className="text-brass" />
              <div>
                <p className="font-medium text-ink">Download Excel (.xlsx)</p>
                <p className="text-xs text-slate-light">
                  Native Excel workbook
                </p>
              </div>
            </button>
          </div>
        )}
      </div>

      {/* Hidden report rendered into the browser's print output only. */}
      {printRows && (
        <div className="print-only" aria-hidden="true">
          <style>{PRINT_STYLES}</style>
          <h2>{title}</h2>
          <table>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column.key}>{column.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {printRows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {columns.map((column) => (
                    <td key={column.key}>
                      {row[column.key] === null ||
                      row[column.key] === undefined
                        ? ""
                        : String(row[column.key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
