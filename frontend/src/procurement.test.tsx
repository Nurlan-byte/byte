import { test, expect, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  stockAssumptions,
  parseCatalog,
  demoCatalog,
  syntheticProcurementCsv,
} from "./procurement";
import { PlanningSection } from "./PlanningSection";
import { methodText, procurementNotes } from "./explanations";

test("procurement explains an unconfirmed manual order and ML fallback in owner language", () => {
  const notes = procurementNotes({
    product_id: "tea",
    name: "Чай",
    batch_size: 1,
    max_daily_qty: 100,
    forecast_mean: 12,
    recommended_qty: 6,
    confirmed_qty: null,
    method: "manual",
    target_is_open: true,
    usable_stock_assumption: null,
    unknown_open_n: 9,
    unknown_stockout_n: 4,
    max_constraint_active: true,
    fallback_reason: "not_better_than_b1_validation",
    warnings: ["ML fallback: not_better_than_b1_validation"],
  });
  expect(methodText("manual")).toBe("Нужно указать количество вручную");
  expect(notes.join(" ")).toContain("режим работы неизвестен в 9 строках");
  expect(notes.join(" ")).toContain("Максимум поставщика");
  expect(notes.join(" ")).toContain("не показала улучшения");
  expect(notes.join(" ")).not.toContain("not_better_than_b1_validation");
});

test("unknown stock is omitted while explicit zero is retained", () => {
  expect(stockAssumptions({ tea: "", juice: "0" })).toEqual({ juice: 0 });
  for (const value of ["-1", "1.5", "NaN", "9007199254740992"])
    expect(() => stockAssumptions({ tea: value })).toThrow();
});
test("generic catalog supports unlimited orders but rejects invalid packs and duplicates", () => {
  expect(
    parseCatalog('[{"product_id":"tea","name":"Tea","pack_size":6}]')[0]
      .max_order_qty,
  ).toBeNull();
  expect(parseCatalog(demoCatalog)[1].max_order_qty).toBeNull();
  for (const pack_size of [0, -1, 1.5])
    expect(() =>
      parseCatalog(
        JSON.stringify([
          { product_id: "tea", name: "Tea", pack_size, max_order_qty: null },
        ]),
      ),
    ).toThrow();
  expect(() =>
    parseCatalog(
      JSON.stringify([
        parseCatalog(demoCatalog)[0],
        parseCatalog(demoCatalog)[0],
      ]),
    ),
  ).toThrow();
  expect(syntheticProcurementCsv().split("\n")).toHaveLength(169);
  const lastClosing = new Map<string, number>();
  for (const row of syntheticProcurementCsv().split("\n").slice(1)) {
    const [, id, opening, received, sold, waste, closing] = row.split(",");
    if (lastClosing.has(id)) expect(Number(opening)).toBe(lastClosing.get(id));
    expect(Number(opening) + Number(received)).toBe(
      Number(sold) + Number(waste) + Number(closing),
    );
    lastClosing.set(id, Number(closing));
  }
});
test("procurement empty state describes ordering and unknown availability without bakery copy", () => {
  const noop = vi.fn();
  const html = renderToStaticMarkup(
    <PlanningSection
      planningKind="procurement"
      dataset={null}
      plan={null}
      busy={false}
      setNotice={noop}
      target="2026-09-01"
      setTarget={noop}
      cutoff="2026-08-23"
      setCutoff={noop}
      method="B1"
      setMethod={noop}
      targetOpen={true}
      setTargetOpen={noop}
      quantities={{}}
      setQuantities={noop}
      confirmation={{ current: null }}
      invalid={false}
      run={async (f) => f()}
      show={noop}
      refreshHistory={async () => {}}
    />,
  );
  expect(html).toContain("Сколько заказываем?");
  expect(html).toContain("остаток неизвестен");
  expect(html).not.toMatch(/выпеч|выпека|выпуск/i);
});

test("mode filtering rejects datasets and plans from the opposite mode, including legacy production", async () => {
  const { forPlanningKind } = await import("./workspace");
  const legacy = { id: "legacy", planning_kind: undefined };
  const production = { id: "production", planning_kind: "production" };
  const procurement = { id: "procurement", planning_kind: "procurement" };
  expect(forPlanningKind(procurement, "production")).toBeNull();
  expect(forPlanningKind(production, "procurement")).toBeNull();
  expect(forPlanningKind(legacy, "procurement")).toBeNull();
  expect(forPlanningKind(legacy, "production")).toBe(legacy);
  expect(forPlanningKind(procurement, "procurement")).toBe(procurement);
});

test("procurement model selection metrics are explicitly internal validation", async () => {
  const { ModelEvaluation } = await import("./ModelSection");
  const html = renderToStaticMarkup(
    <ModelEvaluation
      procurement
      evaluation={{
        paired_n: 20,
        b1_mae: 4,
        ml_mae: 3,
        selection_performed: true,
      }}
      unitLabel="шт."
    />,
  );
  expect(html).toContain("Качество прогноза");
  expect(html).toContain("не является независимой оценкой точности");
  expect(html).not.toContain("MAE");
});
