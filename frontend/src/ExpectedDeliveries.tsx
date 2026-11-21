import type { CatalogItem, ExpectedDelivery } from "./types";
import { MAX_QUANTITY } from "./quantities";

type Props = {
  catalog: CatalogItem[];
  deliveries: ExpectedDelivery[];
  onChange: (deliveries: ExpectedDelivery[]) => void;
  target: string;
  disabled: boolean;
};

export function ExpectedDeliveries({
  catalog,
  deliveries,
  onChange,
  target,
  disabled,
}: Props) {
  function change(index: number, update: Partial<ExpectedDelivery>) {
    onChange(
      deliveries.map((row, i) => (i === index ? { ...row, ...update } : row)),
    );
  }
  return (
    <details className="expected-deliveries incoming-list">
      <summary>Товары в пути ({deliveries.length})</summary>
      <p>
        Добавьте уже заказанные товары, которые ожидаете получить к открытию в
        указанную дату. В остатке выше укажите только количество без этих
        поставок.
      </p>
      {deliveries.map((row, index) => (
        <fieldset className="delivery-fields" key={index} disabled={disabled}>
          <legend>Поставка {index + 1}</legend>
          <label>
            Товар
            <select
              value={row.product_id}
              onChange={(e) => change(index, { product_id: e.target.value })}
            >
              {catalog.map((product) => (
                <option key={product.product_id} value={product.product_id}>
                  {product.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Дата поступления
            <input
              type="date"
              value={row.arrival_date}
              onChange={(e) => change(index, { arrival_date: e.target.value })}
            />
          </label>
          <label>
            Количество, шт.
            <input
              type="number"
              min={1}
              max={MAX_QUANTITY}
              step={1}
              value={row.quantity || ""}
              onChange={(e) =>
                change(index, { quantity: Number(e.target.value) })
              }
            />
          </label>
          <button
            className="secondary"
            type="button"
            onClick={() => onChange(deliveries.filter((_, i) => i !== index))}
          >
            Убрать
          </button>
          {row.arrival_date && target && row.arrival_date > target && (
            <small>Поступит после даты продаж. В этот заказ не входит.</small>
          )}
        </fieldset>
      ))}
      <button
        className="secondary"
        type="button"
        disabled={disabled || deliveries.length >= 500}
        onClick={() =>
          onChange([
            ...deliveries,
            {
              product_id: catalog[0].product_id,
              arrival_date: target,
              quantity: 0,
            },
          ])
        }
      >
        Добавить поставку
      </button>
    </details>
  );
}
