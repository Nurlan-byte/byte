import { expect, test, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelEvaluation } from "./ModelSection";
import { HistorySection } from "./HistorySection";
import { App } from "./App";

const evaluation = {
  b1_mae: 0.416667,
  ml_mae: 1.058562,
  paired_n: 60,
  coverage_denominator_n: 60,
  coverage: 1,
};
test("owner summary explains the comparison without technical metrics", () => {
  const html = renderToStaticMarkup(
    <ModelEvaluation evaluation={evaluation} unitLabel="шт." />,
  );
  expect(html).toContain("Экспериментальный расчёт на проверке оказался менее точным");
  expect(html).toContain("проверьте количество");
  for (const technical of ["MAE", "0,416667", "1,058562", "60/60"])
    expect(html).not.toContain(technical);
});
test("model comparison handles better, equal, missing and zero-pair evidence honestly", () => {
  for (const [values, expected] of [
    [{ ...evaluation, ml_mae: 0.2 }, "прошёл проверку"],
    [{ ...evaluation, ml_mae: evaluation.b1_mae }, "прошёл проверку"],
    [{ ...evaluation, ml_mae: null }, "Недостаточно данных"],
    [{ ...evaluation, paired_n: 0 }, "Недостаточно данных"],
    [undefined, "Качество прогноза пока не проверено"],
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
test("home offers one primary start action and accessible navigation without premature controls", () => {
  const html = renderToStaticMarkup(<App />);
  expect(html).toContain('aria-current="page" aria-controls="panel-planning"');
  expect(html).toContain("Подготовим выпечку на день");
  expect(html).not.toContain("Рассчитать план");
  expect(html).toContain('id="panel-import" hidden=""');
  expect(html).toContain('id="panel-model" hidden=""');
  expect(html).toContain('id="panel-history" hidden=""');
  expect(html).toContain('aria-label="Разделы BakePlan"');
  expect(html).toContain("Дополнительно");
  expect(html).toContain("Качество прогноза");
  expect(html).not.toContain('for="plan-date"');
  expect(html).not.toContain('for="plan-cutoff"');
  expect(html).not.toContain('for="plan-open"');
});
