import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { businessDate } from "./dates";
import { MAX_QUANTITY, validQuantity } from "./quantities";
import type { CatalogItem, RegisteredDelivery } from "./types";

type Props = {
  datasetId: string;
  branch: string;
  catalog: CatalogItem[];
  disabled: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
};

type Receipt = { quantity: number; idempotency_key: string };
const pendingReceipts = new Map<string, Receipt>();

function pendingReceipt(key: string): Receipt | null {
  try {
    const stored = JSON.parse(sessionStorage.getItem(key) ?? "null");
    if (
      stored &&
      typeof stored.idempotency_key === "string" &&
      Number.isSafeInteger(stored.quantity) &&
      stored.quantity > 0
    )
      return stored;
  } catch {
    /* The tab can still retry from memory if browser storage is disabled. */
  }
  return pendingReceipts.get(key) ?? null;
}

function rememberReceipt(key: string, value: Receipt | null) {
  if (value) pendingReceipts.set(key, value);
  else pendingReceipts.delete(key);
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    else sessionStorage.removeItem(key);
  } catch {
    /* Keep the in-memory copy. */
  }
}

const deliveryStatuses: Record<
  RegisteredDelivery["status"],
  { label: string; badge: string }
> = {
  ordered: { label: "В пути", badge: "badge-neutral" },
  partial: { label: "Принята частично", badge: "badge-warning" },
  received: { label: "Принята", badge: "badge-success" },
  cancelled: { label: "Отменена", badge: "badge-neutral" },
};

function DeliveryRow({
  row,
  name,
  path,
  saved,
  run,
  disabled,
}: {
  row: RegisteredDelivery;
  name: string;
  path: string;
  saved: (row: RegisteredDelivery) => Promise<void>;
  run: Props["run"];
  disabled: boolean;
}) {
  const key = `bakeplan.receipt.${row.bakery_id}.${row.id}`;
  const [pending, setPending] = useState(() => pendingReceipt(key));
  const [quantity, setQuantity] = useState("");
  const [newDate, setNewDate] = useState("");
  const open = row.status === "ordered" || row.status === "partial";
  const status = deliveryStatuses[row.status];

  function receive() {
    void run(async () => {
      const receipt = pending ?? {
        quantity: Number(quantity),
        idempotency_key: crypto.randomUUID(),
      };
      rememberReceipt(key, receipt);
      setPending(receipt);
      let updated: RegisteredDelivery;
      try {
        updated = await api<RegisteredDelivery>(path + "/receipts", receipt);
      } catch (error) {
        if (
          error instanceof ApiError &&
          [404, 409, 422].includes(error.status)
        ) {
          rememberReceipt(key, null);
          setPending(null);
          throw error;
        }
        throw new Error(
          `Статус приёмки ${receipt.quantity} шт. по поставке ${row.number} неизвестен. В реестре нажмите «Повторить приёмку».`,
        );
      }
      rememberReceipt(key, null);
      setPending(null);
      setQuantity("");
      await saved(updated);
    });
  }

  return (
    <article className="delivery-record" aria-label={`${row.number} · ${name}`}>
      <div className="delivery-record-heading">
        <div>
          <h4>
            {row.number} · {name}
          </h4>
          <p>
            Ожидаемая дата{" "}
            <time dateTime={row.arrival_date}>{row.arrival_date}</time>
          </p>
        </div>
        <span className={`badge ${status.badge}`}>{status.label}</span>
      </div>
      <dl className="delivery-totals">
        <div>
          <dt>Заказано</dt>
          <dd>{row.ordered_qty} шт.</dd>
        </div>
        <div>
          <dt>Принято</dt>
          <dd>{row.received_qty} шт.</dd>
        </div>
        <div>
          <dt>В пути</dt>
          <dd>{row.remaining_qty} шт.</dd>
        </div>
      </dl>
      {open && (
        <progress
          aria-hidden="true"
          value={row.received_qty}
          max={row.ordered_qty}
        />
      )}
      {open && row.arrival_date < businessDate() && (
        <p className="stock-rule">
          Срок прошёл. Примите товар или перенесите дату, чтобы учесть поставку
          в новом заказе.
        </p>
      )}
      {pending ? (
        <div>
          <p>
            Ожидается подтверждение приёмки {pending.quantity} шт. Повтор
            использует тот же номер операции.
          </p>
          <button type="button" disabled={disabled} onClick={receive}>
            Повторить приёмку
          </button>
        </div>
      ) : (
        open && (
          <>
            <div className="delivery-fields">
              <label>
                Получено сейчас, шт.
                <input
                  type="number"
                  min="1"
                  max={row.remaining_qty}
                  step="1"
                  value={quantity}
                  disabled={disabled}
                  onChange={(e) => setQuantity(e.target.value)}
                />
              </label>
              <button
                type="button"
                disabled={
                  disabled ||
                  !validQuantity(quantity, 1, row.remaining_qty) ||
                  Number(quantity) === 0
                }
                onClick={receive}
              >
                Принять
              </button>
            </div>
            <details>
              <summary>Изменить поставку</summary>
              <div className="delivery-fields">
                <label>
                  Новая дата поступления
                  <input
                    type="date"
                    value={newDate}
                    disabled={disabled}
                    onChange={(e) => setNewDate(e.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="secondary"
                  disabled={
                    disabled || !newDate || newDate === row.arrival_date
                  }
                  onClick={() =>
                    void run(async () => {
                      const updated = await api<RegisteredDelivery>(
                        path,
                        { arrival_date: newDate },
                        "PATCH",
                      );
                      setNewDate("");
                      await saved(updated);
                    })
                  }
                >
                  Перенести дату
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={disabled}
                  onClick={() => {
                    if (
                      window.confirm(
                        `Отменить оставшиеся ${row.remaining_qty} шт. по поставке ${row.number}?`,
                      )
                    )
                      void run(async () =>
                        saved(
                          await api<RegisteredDelivery>(path + "/cancel", {}),
                        ),
                      );
                  }}
                >
                  Отменить остаток
                </button>
              </div>
            </details>
          </>
        )
      )}
    </article>
  );
}

function RegistryContents({ datasetId, catalog, disabled, run }: Props) {
  const path = `/datasets/${encodeURIComponent(datasetId)}/deliveries`;
  const [rows, setRows] = useState<RegisteredDelivery[] | null>(null);
  const [error, setError] = useState("");
  const [number, setNumber] = useState("");
  const [product, setProduct] = useState(catalog[0].product_id);
  const [date, setDate] = useState(() => businessDate(1));
  const [quantity, setQuantity] = useState("");
  useEffect(() => {
    let active = true;
    void api<RegisteredDelivery[]>(path).then(
      (value) => {
        if (active) setRows(value);
      },
      (reason) => {
        if (active)
          setError(String(reason instanceof Error ? reason.message : reason));
      },
    );
    return () => {
      active = false;
    };
  }, [path]);

  async function refresh() {
    setRows(await api<RegisteredDelivery[]>(path));
    setError("");
  }
  async function saved(updated: RegisteredDelivery) {
    setRows((current) => [
      updated,
      ...(current ?? []).filter((row) => row.id !== updated.id),
    ]);
    try {
      await refresh();
    } catch {
      setError(
        "Операция сохранена. Обновите реестр, чтобы увидеть последние изменения.",
      );
    }
  }
  return (
    <>
      <div className="registry-toolbar">
        <p>
          Укажите фактически полученное количество. При следующем расчёте
          включите принятые единицы в доступный остаток.
        </p>
        {error && <p role="alert">{error}</p>}
        {rows === null && !error ? (
          <p role="status">Загрузка поставок…</p>
        ) : (
          <button
            type="button"
            className="secondary"
            disabled={disabled}
            onClick={() => void run(refresh)}
          >
            Обновить реестр
          </button>
        )}
      </div>
      {rows && (
        <>
          {rows.length === 0 && <p>Поставок пока нет.</p>}
          <fieldset className="delivery-fields" disabled={disabled}>
            <legend>Новая поставка</legend>
            <label>
              Номер поставки
              <input
                type="text"
                maxLength={100}
                value={number}
                onChange={(e) => setNumber(e.target.value)}
              />
            </label>
            <label>
              Товар поставки
              <select
                value={product}
                onChange={(e) => setProduct(e.target.value)}
              >
                {catalog.map((item) => (
                  <option key={item.product_id} value={item.product_id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Ожидаемая дата
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </label>
            <label>
              Заказано, шт.
              <input
                type="number"
                min="1"
                max={MAX_QUANTITY}
                step="1"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
            </label>
            <button
              type="button"
              disabled={
                !number.trim() ||
                !date ||
                !validQuantity(quantity, 1, MAX_QUANTITY) ||
                Number(quantity) === 0
              }
              onClick={() =>
                void run(async () => {
                  const updated = await api<RegisteredDelivery>(path, {
                    number: number.trim(),
                    product_id: product,
                    arrival_date: date,
                    ordered_qty: Number(quantity),
                  });
                  setNumber("");
                  setQuantity("");
                  await saved(updated);
                })
              }
            >
              Записать поставку
            </button>
          </fieldset>
          <div className="delivery-list">
            {rows.map((row) => (
              <DeliveryRow
                key={row.id}
                row={row}
                path={`${path}/${encodeURIComponent(row.id)}`}
                name={
                  catalog.find((item) => item.product_id === row.product_id)
                    ?.name ?? row.product_id
                }
                saved={saved}
                run={run}
                disabled={disabled}
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}

export function DeliveryRegistry(props: Props) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="data-guide delivery-registry"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>Реестр поставок · {props.branch}</summary>
      {open && <RegistryContents {...props} />}
    </details>
  );
}
