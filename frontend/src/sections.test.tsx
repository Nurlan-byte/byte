import { expect, test, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PlanningSection } from "./PlanningSection";
import { ModelSection } from "./ModelSection";
import type { Dataset, Plan } from "./types";

const units: Dataset = {
  id: "units-data",
  source: "synthetic",
  bakery_id: "test",
  target: "sold_units",
  unit: "physical_unit",
  schema_version: "history-1",
  provenance: "Synthetic test",
  metadata_declared: true,
};
const plan: Plan = {
  id: "plan-1",
  dataset_id: units.id,
  target_date: "2026-10-12",
  cutoff: "2026-10-11",
  source: "synthetic",
  status: "draft",
  revision: 0,
  model_id: "model-1",
  protocol_version: "experimental-test",
  items: [
    {
      product_id: "p1",
      name: "Хлеб",
      batch_size: 12,
      max_daily_qty: 100,
      forecast_mean: 50,
      recommended_qty: 48,
      confirmed_qty: null,
      method: "B1",
      fallback_reason: "Недостаточно данных модели",
      model_id: "model-1",
      warnings: [],
    },
  ],
};

function planningProps(
  dataset: Dataset,
): Parameters<typeof PlanningSection>[0] {
  return {
    dataset,
    plan,
    busy: false,
    setNotice: vi.fn(),
    target: "2026-10-12",
    setTarget: vi.fn(),
    cutoff: "2026-10-11",
    setCutoff: vi.fn(),
    method: "ML",
    setMethod: vi.fn(),
    targetOpen: true,
    setTargetOpen: vi.fn(),
    quantities: { p1: "48" },
    setQuantities: vi.fn(),
    confirmation: { current: null },
    invalid: false,
    run: async (operation) => operation(),
    show: vi.fn(),
    refreshHistory: async () => {},
  };
}

test("sales-only target or item-line unit hides production even with a previous units plan", () => {
  for (const dataset of [
    {
      ...units,
      target: "item_line_count" as const,
      unit: "item_line" as const,
    },
    { ...units, unit: "item_line" as const },
  ]) {
    const html = renderToStaticMarkup(
      <PlanningSection {...planningProps(dataset)} />,
    );
    expect(html).toContain("не преобразуются в физические штуки");
    expect(html).not.toContain("Рассчитать план");
    expect(html).not.toContain("Подтвердить и сохранить");
    expect(html).not.toContain('type="number"');
  }
});

test("units plan keeps batch constraints and actual fallback/model lineage", () => {
  const html = renderToStaticMarkup(
    <PlanningSection {...planningProps(units)} />,
  );
  expect(html).toContain('value="ML"');
  expect(html).toContain('step="12"');
  expect(html).toContain('max="96"');
  expect(html).toContain("model-1");
  expect(html).toContain("experimental-test");
  expect(html).toContain("Недостаточно данных модели");
  expect(html).toContain("Подтвердить и сохранить");
});

test("legacy metadata visibly disables training until explicit reimport", () => {
  const props = planningProps({ ...units, metadata_declared: false });
  const html = renderToStaticMarkup(
    <ModelSection {...props} dataset={props.dataset!} />,
  );
  expect(html).toContain("повторно импортируйте исходный CSV");
  expect(html).toMatch(/<button disabled="">Обучить модель<\/button>/);
  expect(html).toContain("Получить прогноз");
  expect(html).not.toContain('type="number"');
});
