import { useRef, useState } from "react";
import { MAX_QUANTITY, validQuantity } from "./quantities";
import type { Plan } from "./types";

function CopyOrder({ text }: { text: string }) {
  const field = useRef<HTMLTextAreaElement>(null);
  const [feedback, setFeedback] = useState("");

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setFeedback("Заказ скопирован.");
    } catch {
      field.current?.focus();
      field.current?.select();
      setFeedback("Скопируйте выделенный текст вручную.");
    }
  }

  return (
    <>
      <div className="supplier-copy-actions">
        <button className="secondary" type="button" onClick={() => void copy()}>
          Скопировать заказ
        </button>
        <span className="muted" aria-live="polite">
          {feedback}
        </span>
      </div>
      <textarea
        ref={field}
        aria-label="Текст заказа для поставщика"
        value={text}
        rows={Math.min(text.split("\n").length, 10)}
        readOnly
      />
    </>
  );
}

export function SupplierOrderText({ plan }: { plan: Plan }) {
  if (plan.status !== "confirmed" || plan.planning_kind !== "procurement")
    return null;

  const lines: string[] = [];
  let problem = "";
  for (const item of plan.items) {
    const quantity = item.confirmed_qty;
    const pack = item.pack_size;
    const name = item.name?.trim().replace(/\s+/g, " ") ?? "";
    if (
      typeof quantity !== "number" ||
      (pack != null && (!Number.isSafeInteger(pack) || pack < 1)) ||
      !validQuantity(
        String(quantity),
        pack ?? 1,
        item.max_order_qty ?? MAX_QUANTITY,
      ) ||
      (quantity > 0 && (!name || item.target_is_open === false))
    ) {
      problem = "Проверьте сохранённые позиции и подтвердите заказ ещё раз.";
      break;
    }
    if (quantity === 0) continue;
    const packs =
      pack != null ? ` (${quantity / pack} уп. по ${pack} шт.)` : "";
    lines.push(`${name} — ${quantity} шт.${packs}`);
  }
  const text = [
    "Заказ поставщику",
    ...(plan.order_date ? [`Дата заказа: ${plan.order_date}`] : []),
    `Дата продаж: ${plan.target_date}`,
    "",
    ...lines,
  ].join("\n");
  return (
    <section className="supplier-message" aria-label="Сообщение поставщику">
      <h3>Сообщение поставщику</h3>
      {problem || lines.length === 0 ? (
        <p>{problem || "В подтверждённом заказе нет товаров к поставке."}</p>
      ) : (
        <CopyOrder
          key={JSON.stringify([plan.id, plan.revision, text])}
          text={text}
        />
      )}
    </section>
  );
}
