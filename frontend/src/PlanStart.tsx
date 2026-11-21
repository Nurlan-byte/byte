import { Icon } from "./Icon";

type StartProps = {
  procurement?: boolean;
  busy: boolean;
  onImport?: () => void;
  onHistory?: () => void;
};

export function PlanStart({
  procurement,
  busy,
  onImport,
  onHistory,
}: StartProps) {
  return (
    <section
      id="planning"
      className="plan-start"
      aria-labelledby="planning-heading"
    >
      <div className="plan-start-hero">
        <div className="plan-start-copy">
          <h2 id="planning-heading">
            {procurement ? "Сколько заказываем?" : "Подготовим выпечку на день"}
          </h2>
          <p>
            {procurement
              ? "Добавьте историю продаж и каталог. BakePlan рассчитает заказ с учётом упаковок, остатков и товаров в пути."
              : "Загрузите историю продаж, чтобы рассчитать партии для каждого изделия и подтвердить выпуск на смену."}
          </p>
          <button
            className="cta"
            type="button"
            disabled={busy}
            onClick={onImport}
          >
            Перейти к данным <Icon name="arrow" />
          </button>
          <small>Можно начать с демонстрационного примера.</small>
        </div>
        {procurement ? (
          <div className="plan-start-requirements">
            <h3>Для первого заказа</h3>
            <dl>
              <div>
                <dt>Продажи</dt>
                <dd>История по дням и товарам</dd>
              </div>
              <div>
                <dt>Каталог</dt>
                <dd>Размер упаковки и лимит заказа</dd>
              </div>
              <div>
                <dt>Остаток</dt>
                <dd>Если остаток неизвестен, количество выбираете вы</dd>
              </div>
            </dl>
          </div>
        ) : (
          <img className="plan-start-photo" src="/images/bakery.webp" alt="" />
        )}
      </div>
      <div className="plan-start-history">
        <span className="plan-start-history-icon">
          <Icon name="history" />
        </span>
        <div>
          <h3>{procurement ? "Сохранённые заказы" : "Сохранённые планы"}</h3>
          <p>В истории можно открыть предыдущую версию и скачать CSV.</p>
        </div>
        <button
          className="secondary"
          type="button"
          disabled={busy}
          onClick={onHistory}
        >
          Открыть сохранённые планы <Icon name="arrow" />
        </button>
      </div>
    </section>
  );
}
