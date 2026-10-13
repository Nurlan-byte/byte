import { expect, test, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelEvaluation } from "./ModelSection";
import { HistorySection } from "./HistorySection";
import { App } from "./App";

const evaluation = {
  b1_mae: 0.416667,
  ml_mae: 0.767225,
  paired_n: 60,
  coverage_denominator_n: 60,
  coverage: 1,
};
test("model comparison exposes exact synthetic evidence without implying real accuracy", () => {
  const html = renderToStaticMarkup(
    <ModelEvaluation evaluation={evaluation} unitLabel="шт." />,
  );
  for (const text of [
    "MAE B1",
    "MAE ML",
    "0,416667",
    "0,767225",
    "Парное N",
    "60/60",
    "Чем ниже, тем лучше",
    "ML хуже B1",
    "не доказательство",
  ])
    expect(html).toContain(text);
  expect(html.indexOf("ML хуже B1")).toBeLessThan(html.indexOf("<details>"));
});
test("model comparison handles better, equal, missing and zero-pair evidence honestly", () => {
  for (const [values, expected] of [
    [{ ...evaluation, ml_mae: 0.2 }, "ошибка ML ниже"],
    [{ ...evaluation, ml_mae: evaluation.b1_mae }, "ошибка ML и B1 одинакова"],
    [{ ...evaluation, ml_mae: null }, "Недостаточно сопоставимых данных"],
    [{ ...evaluation, paired_n: 0 }, "Недостаточно сопоставимых данных"],
    [undefined, "Оценка качества не предоставлена"],
  ] as const)
    expect(
      renderToStaticMarkup(
        <ModelEvaluation evaluation={values} unitLabel="шт." />,
      ),
    ).toContain(expected);
});
test("history uses readable statuses and exact saved export paths, locking actions while busy", () => {
  for (const busy of [false, true]) {
    const html = renderToStaticMarkup(
      <HistorySection
        history={[
          {
            id: "plan-one",
            dataset_id: "saved-data",
            target_date: "2026-10-13",
            source: "synthetic",
            status: "confirmed",
            revision: 1,
          },
        ]}
        offset={0}
        loaded
        busy={busy}
        onLoad={vi.fn()}
        onPage={vi.fn()}
      />,
    );
    for (const text of [
      "13 октября 2026",
      "Подтверждён",
      "Синтетические данные",
      "Открыть план",
      "Экспорт CSV",
      "редакция 1",
    ])
      expect(html).toContain(text);
    expect(html.includes('href="/api/plans/plan-one/export"')).toBe(!busy);
    if (busy) expect(html).toContain('aria-disabled="true"');
  }
});
test("home is Plan, B1 remains selected and navigation has stable keyboard-accessible labels", () => {
  const html = renderToStaticMarkup(<App />);
  expect(html).toContain('aria-current="page" aria-controls="panel-planning"');
  expect(html).toContain('<option value="B1" selected="">');
  expect(html).toContain('id="panel-import" hidden=""');
  expect(html).toContain('id="panel-model" hidden=""');
  expect(html).toContain('id="panel-history" hidden=""');
  expect(html).toContain('aria-label="Разделы BakePlan"');
  expect(html).toContain('aria-hidden="true">ML</span>');
  expect(html).toContain('for="plan-date"');
  expect(html).toContain('for="plan-cutoff"');
  expect(html).toContain('for="plan-open"');
});
