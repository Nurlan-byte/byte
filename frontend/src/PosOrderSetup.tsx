import { useState } from "react";
import { api } from "./api";
import type { Dataset } from "./types";
import type { SectionProps } from "./sectionProps";

export function PosOrderSetup({
  dataset,
  busy,
  run,
  onReady,
}: {
  dataset: Dataset;
  busy: boolean;
  run: SectionProps["run"];
  onReady: (dataset: Dataset) => Promise<void>;
}) {
  const [packs, setPacks] = useState<Record<string, string>>({});
  const entries = Object.entries(dataset.product_names ?? {});
  if (
    !dataset.forecast_only ||
    dataset.schema_version !== "pos-units-1" ||
    !entries.length
  )
    return null;
  const valid = (value: string) =>
    /^\d+$/.test(value) && Number(value) > 0 && Number(value) <= 1_000_000_000;
  return (
    <details className="demand-source">
      <summary>Подготовить заказ поставщику</summary>
      <p>
        Укажите, сколько штук поставщик кладёт в одну упаковку каждого товара.
        Продажи уже загружены. Остатки и срок поставки можно указать на
        следующем экране.
      </p>
      <button
        type="button"
        className="secondary"
        disabled={busy}
        onClick={() =>
          setPacks(Object.fromEntries(entries.map(([id]) => [id, "1"])))
        }
      >
        Все товары по одной штуке
      </button>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            const result = await api<Dataset>(
              `/datasets/${dataset.id}/order-catalog`,
              {
                packs: Object.fromEntries(
                  entries.map(([id]) => [id, Number(packs[id])]),
                ),
              },
            );
            await onReady(result);
          });
        }}
      >
        <div className="controls">
          {entries.map(([id, name]) => (
            <label className="control-field" key={id}>
              {name} · штук в упаковке
              <input
                aria-label={`Штук в упаковке: ${name}`}
                inputMode="numeric"
                type="number"
                required
                step={1}
                min={1}
                max={1000000000}
                disabled={busy}
                value={packs[id] ?? ""}
                onChange={(e) => setPacks({ ...packs, [id]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <button
          disabled={busy || entries.some(([id]) => !valid(packs[id] ?? ""))}
        >
          Перейти к расчёту заказа
        </button>
      </form>
    </details>
  );
}
