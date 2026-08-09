/**
 * Minimal, dependency-free .xlsx writer.
 *
 * Generates a real Office Open XML spreadsheet (the ZIP container with
 * [Content_Types].xml, workbook, worksheet and styles parts) using the
 * STORE (uncompressed) method. Excel and LibreOffice open the result
 * directly with no "format mismatch" warning.
 *
 * The ZIP/XLSX building is pure (no DOM), so `buildExcelWorkbook` is
 * unit-testable in Node; only `downloadExcel` touches the browser.
 */

export interface ExcelColumn {
  key: string;
  label: string;
}

type CellValue = string | number | null | undefined;

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/** 0-based column index -> Excel letters (0 = A, 25 = Z, 26 = AA, …). */
function columnLetters(index: number): string {
  let letters = "";
  let n = index;
  do {
    letters = String.fromCharCode((n % 26) + 65) + letters;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return letters;
}

function cellRef(column: number, rowIndex: number): string {
  return `${columnLetters(column)}${rowIndex + 1}`;
}

function worksheetXml(columns: ExcelColumn[], rows: CellValue[][]): string {
  const escaped = (v: string) => escapeXml(v);
  const parts: string[] = [
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`,
    `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>`,
  ];

  parts.push(`<row r="1">`);
  columns.forEach((column, i) => {
    parts.push(
      `<c r="${cellRef(i, 0)}" t="inlineStr" s="1"><is><t>${escaped(column.label)}</t></is></c>`,
    );
  });
  parts.push(`</row>`);

  rows.forEach((row, r) => {
    parts.push(`<row r="${r + 2}">`);
    for (let i = 0; i < columns.length; i++) {
      const value = row[i];
      const ref = cellRef(i, r + 1);
      if (value === null || value === undefined || value === "") {
        parts.push(`<c r="${ref}"/>`);
      } else if (typeof value === "number") {
        parts.push(`<c r="${ref}"><v>${value}</v></c>`);
      } else {
        parts.push(
          `<c r="${ref}" t="inlineStr"><is><t>${escaped(value)}</t></is></c>`,
        );
      }
    }
    parts.push(`</row>`);
  });

  parts.push(`</sheetData></worksheet>`);
  return parts.join("");
}

function stylesXml(): string {
  return [
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`,
    `<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`,
    `<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>`,
    `<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>`,
    `<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>`,
    `<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>`,
    `<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>`,
    `<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>`,
    `</styleSheet>`,
  ].join("");
}

function contentTypesXml(): string {
  return [
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`,
    `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">`,
    `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>`,
    `<Default Extension="xml" ContentType="application/xml"/>`,
    `<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>`,
    `<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`,
    `<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>`,
    `</Types>`,
  ].join("");
}

function rootRelsXml(): string {
  return [
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`,
    `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`,
    `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>`,
    `</Relationships>`,
  ].join("");
}

function workbookXml(): string {
  return [
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`,
    `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">`,
    `<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets>`,
    `</workbook>`,
  ].join("");
}

function workbookRelsXml(): string {
  return [
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`,
    `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`,
    `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>`,
    `<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>`,
    `</Relationships>`,
  ].join("");
}

// ---------------------------------------------------------------------------
// CRC-32 (IEEE 802.3, reflected polynomial 0xEDB88320) — required by ZIP.
// ---------------------------------------------------------------------------

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc = CRC_TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date: Date): { time: number; date: number } {
  const y = date.getFullYear();
  const time =
    (date.getHours() << 11) |
    (date.getMinutes() << 5) |
    Math.floor(date.getSeconds() / 2);
  const dateBits = ((Math.max(1980, y) - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { time: time & 0xffff, date: dateBits & 0xffff };
}

// ---------------------------------------------------------------------------
// ZIP writer (STORE method, no compression) — enough for the small XML parts.
// ---------------------------------------------------------------------------

interface ZipEntry {
  name: string;
  data: Uint8Array;
}

function buildZip(entries: ZipEntry[]): Uint8Array {
  const encoder = new TextEncoder();
  const localParts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  let offset = 0;
  const stamp = dosDateTime(new Date());

  const pushU16 = (bytes: Uint8Array, at: number, value: number) => {
    bytes[at] = value & 0xff;
    bytes[at + 1] = (value >>> 8) & 0xff;
  };
  const pushU32 = (bytes: Uint8Array, at: number, value: number) => {
    bytes[at] = value & 0xff;
    bytes[at + 1] = (value >>> 8) & 0xff;
    bytes[at + 2] = (value >>> 16) & 0xff;
    bytes[at + 3] = (value >>> 24) & 0xff;
  };

  for (const entry of entries) {
    const nameBytes = encoder.encode(entry.name);
    const data = entry.data;
    const checksum = crc32(data);
    const size = data.length;
    const nameLen = nameBytes.length;

    const local = new Uint8Array(30 + nameLen);
    pushU32(local, 0, 0x04034b50);
    pushU16(local, 4, 20); // version needed to extract
    pushU16(local, 6, 0); // general purpose flags
    pushU16(local, 8, 0); // compression method: stored
    pushU16(local, 10, stamp.time);
    pushU16(local, 12, stamp.date);
    pushU32(local, 14, checksum);
    pushU32(local, 18, size);
    pushU32(local, 22, size);
    pushU16(local, 26, nameLen);
    pushU16(local, 28, 0); // extra field length
    local.set(nameBytes, 30);
    localParts.push(local, data);

    const central = new Uint8Array(46 + nameLen);
    pushU32(central, 0, 0x02014b50);
    pushU16(central, 4, 20); // version made by
    pushU16(central, 6, 20); // version needed to extract
    pushU16(central, 8, 0); // flags
    pushU16(central, 10, 0); // method
    pushU16(central, 12, stamp.time);
    pushU16(central, 14, stamp.date);
    pushU32(central, 16, checksum);
    pushU32(central, 20, size);
    pushU32(central, 24, size);
    pushU16(central, 28, nameLen);
    pushU16(central, 30, 0); // extra
    pushU16(central, 32, 0); // comment
    pushU16(central, 34, 0); // disk start
    pushU16(central, 36, 0); // internal attrs
    pushU32(central, 38, 0); // external attrs
    pushU32(central, 42, offset); // offset of local header
    central.set(nameBytes, 46);
    centralParts.push(central);

    offset += local.length + size;
  }

  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const eocd = new Uint8Array(22);
  pushU32(eocd, 0, 0x06054b50);
  pushU16(eocd, 4, 0); // disk number
  pushU16(eocd, 6, 0); // central directory disk
  pushU16(eocd, 8, entries.length); // entries on this disk
  pushU16(eocd, 10, entries.length); // total entries
  pushU32(eocd, 12, centralSize);
  pushU32(eocd, 16, offset);
  pushU16(eocd, 20, 0); // comment length

  const total = offset + centralSize + eocd.length;
  const out = new Uint8Array(total);
  let cursor = 0;
  for (const part of localParts) {
    out.set(part, cursor);
    cursor += part.length;
  }
  for (const part of centralParts) {
    out.set(part, cursor);
    cursor += part.length;
  }
  out.set(eocd, cursor);
  return out;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Builds the complete .xlsx file as bytes. Pure — safe to test in Node.
 */
export function buildExcelWorkbook(
  columns: ExcelColumn[],
  rowData: Record<string, CellValue>[],
): Uint8Array {
  const rows = rowData.map((row) => columns.map((column) => row[column.key]));

  const entries: ZipEntry[] = [
    { name: "[Content_Types].xml", data: new TextEncoder().encode(contentTypesXml()) },
    { name: "_rels/.rels", data: new TextEncoder().encode(rootRelsXml()) },
    { name: "xl/workbook.xml", data: new TextEncoder().encode(workbookXml()) },
    { name: "xl/_rels/workbook.xml.rels", data: new TextEncoder().encode(workbookRelsXml()) },
    { name: "xl/styles.xml", data: new TextEncoder().encode(stylesXml()) },
    { name: "xl/worksheets/sheet1.xml", data: new TextEncoder().encode(worksheetXml(columns, rows)) },
  ];

  return buildZip(entries);
}

/** Triggers a browser download of the generated .xlsx file. */
export function downloadExcel(
  filename: string,
  columns: ExcelColumn[],
  rows: Record<string, CellValue>[],
): void {
  if (rows.length === 0) return;
  const bytes = buildExcelWorkbook(columns, rows);
  // Copy into a fresh buffer so the Blob sees the exact bytes (the builder
  // may return a larger backing ArrayBuffer).
  const buffer = new Uint8Array(bytes).buffer;
  const blob = new Blob([buffer], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.endsWith(".xlsx") ? filename : `${filename}.xlsx`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
