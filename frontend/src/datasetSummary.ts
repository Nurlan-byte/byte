import type { Dataset } from "./types";

type SavedDates = Pick<Dataset, "rows">;

function isCalendarDate(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) ||
    value.startsWith("0000")
  )
    return false;
  const date = new Date(`${value}T00:00:00Z`);
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
}

/** Derive the period only from the persisted API snapshot, never a CSV draft. */
export function datasetDatePeriod(
  dataset: SavedDates | null,
): { start: string; end: string } | null {
  if (!Array.isArray(dataset?.rows) || !dataset.rows.length) return null;
  let start: string | null = null;
  let end: string | null = null;
  for (const row of dataset.rows) {
    // A partial period would be misleading if a legacy snapshot is malformed.
    if (!isCalendarDate(row?.date)) return null;
    if (start === null || row.date < start) start = row.date;
    if (end === null || row.date > end) end = row.date;
  }
  return start !== null && end !== null ? { start, end } : null;
}

export function formatDatasetPeriod(dataset: SavedDates | null): string {
  const period = datasetDatePeriod(dataset);
  if (!period) return "Период не указан";
  const format = (value: string) => value.split("-").reverse().join(".");
  return period.start === period.end
    ? format(period.start)
    : `${format(period.start)} — ${format(period.end)}`;
}

export function datasetSourceLabel(source: string): string {
  switch (source) {
    case "synthetic":
      return "Синтетические данные";
    case "local":
      return "Данные пекарни";
    case "external":
      return "Внешние данные";
    default:
      return source;
  }
}
