import { expect, test } from "vitest";
import {
  datasetDatePeriod,
  datasetSourceLabel,
  formatDatasetPeriod,
} from "./datasetSummary";

test("period uses saved rows across products in any order", () => {
  const saved = {
    rows: [
      { date: "2026-10-11" },
      { date: "2026-07-20" },
      { date: "2026-10-11" },
      { date: "2026-09-01" },
    ],
  };
  expect(datasetDatePeriod(saved)).toEqual({
    start: "2026-07-20",
    end: "2026-10-11",
  });
  expect(formatDatasetPeriod(saved)).toBe("20.07.2026 — 11.10.2026");
});

test("single-day saved data has a single date without an invented range", () => {
  expect(formatDatasetPeriod({ rows: [{ date: "2024-02-29" }] })).toBe(
    "29.02.2024",
  );
});

test("legacy snapshots without saved rows have an explicit unknown period", () => {
  for (const saved of [null, {}, { rows: [] }]) {
    expect(datasetDatePeriod(saved)).toBeNull();
    expect(formatDatasetPeriod(saved)).toBe("Период не указан");
  }
});

test("malformed saved dates do not produce a misleading partial range", () => {
  for (const date of [
    "2026-02-29",
    "2026-02-31",
    "2026-13-01",
    "2026-10-00",
    "2026-2-01",
    "2026-10-01T00:00:00Z",
    "0000-01-01",
    "",
  ]) {
    expect(
      datasetDatePeriod({ rows: [{ date: "2026-10-11" }, { date }] }),
    ).toBeNull();
  }
});

test("source labels preserve unknown source identity", () => {
  expect(datasetSourceLabel("synthetic")).toBe("Синтетические данные");
  expect(datasetSourceLabel("local")).toBe("Данные пекарни");
  expect(datasetSourceLabel("external")).toBe("Внешние данные");
  expect(datasetSourceLabel("legacy_source")).toBe("legacy_source");
  expect(datasetSourceLabel("constructor")).toBe("constructor");
});
