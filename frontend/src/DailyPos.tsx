import { useState } from "react";
import { api } from "./api";
import type { ForecastSnapshot } from "./ForecastResult";
import type { AdoptDataset } from "./DemandSources";
import type { ImportProps } from "./ImportSection";
import type { Dataset } from "./types";
import { workspaceReference } from "./workspace";

export type PosConnectionSummary = {
  id: string;
  provider: "poster" | "moysklad";
  location: { id: string; name: string };
  status: "ready" | "syncing" | "error";
  last_success_day: string | null;
  last_attempt_at: string | null;
  error: string | null;
  dataset_id: string | null;
};

export type DailyForecast = ForecastSnapshot & { warnings: string[] };
export type StoredPosResult = {
  dataset: Dataset;
  forecast: DailyForecast;
  connection: PosConnectionSummary;
};

export function DailyConnections({
  connections,
  dailyAvailable,
  refresh,
  busy,
  run,
  adopt,
}: {
  connections: PosConnectionSummary[];
  dailyAvailable: boolean;
  refresh: () => Promise<void>;
  busy: boolean;
  run: ImportProps["run"];
  adopt: AdoptDataset;
}) {
  const [removing, setRemoving] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const disabled = busy || removing !== null || refreshing;
  return (
    <section aria-label="Ежедневные подключения" className="daily-connections">
      <h3>Ежедневные подключения</h3>
      {connections.map((connection) => (
        <div className="daily-connection" key={connection.id}>
          <strong>{connection.location.name}</strong>
          <span>
            {connection.provider === "poster" ? "Poster POS" : "МойСклад"}
          </span>
          <p>
            {!dailyAvailable
              ? "Обновления приостановлены до восстановления хранилища ключей."
              : connection.status === "syncing"
                ? "Идёт обновление продаж."
                : connection.status === "error"
                  ? connection.error || "Не удалось обновить продажи."
                  : "Ежедневное обновление включено."}
            {dailyAvailable &&
              connection.status === "error" &&
              " BakePlan повторяет попытки каждые 15 минут."}
          </p>
          <p className="muted">
            {connection.last_success_day
              ? `Последнее успешное обновление: ${connection.last_success_day}`
              : "Первого успешного обновления пока нет."}
          </p>
          <div className="actions">
            {connection.dataset_id && (
              <button
                type="button"
                className="secondary"
                disabled={disabled}
                onClick={() =>
                  void run(async () => {
                    const latest = await api<StoredPosResult>(
                      `/pos/connections/${encodeURIComponent(connection.id)}/latest`,
                    );
                    await adopt(latest.dataset, [], true, {
                      summary: latest.connection,
                      forecast: latest.forecast,
                    });
                  })
                }
              >
                Открыть последний прогноз
              </button>
            )}
            {confirmId !== connection.id && (
              <button
                type="button"
                className="secondary"
                disabled={disabled}
                onClick={() => {
                  setConfirmId(connection.id);
                  setRemoveError("");
                }}
              >
                Отключить кассу
              </button>
            )}
          </div>
          {confirmId === connection.id && (
            <div className="status-note">
              <p>
                Удалить сохранённый ключ для «{connection.location.name}» и
                остановить обновления? Загруженные продажи и созданные планы
                сохранятся.
              </p>
              <div className="actions">
                <button
                  type="button"
                  className="secondary"
                  disabled={disabled}
                  onClick={() => setConfirmId(null)}
                >
                  Отмена
                </button>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={async () => {
                    setRemoving(connection.id);
                    setRemoveError("");
                    try {
                      await api<void>(
                        `/pos/connections/${encodeURIComponent(connection.id)}`,
                        undefined,
                        "DELETE",
                      );
                      if (
                        workspaceReference("pos-connection")?.split("/")[1] ===
                        connection.id
                      )
                        workspaceReference("pos-connection", null);
                      setConfirmId(null);
                      await refresh();
                    } catch (error) {
                      setRemoveError(
                        error instanceof Error
                          ? error.message
                          : "Не удалось отключить кассу.",
                      );
                    } finally {
                      setRemoving(null);
                    }
                  }}
                >
                  Удалить ключ и отключить
                </button>
              </div>
            </div>
          )}
        </div>
      ))}
      {removeError && (
        <p role="alert" className="field-error">
          {removeError}
        </p>
      )}
      <button
        type="button"
        className="secondary"
        disabled={disabled}
        onClick={async () => {
          setRefreshing(true);
          try {
            await refresh();
          } finally {
            setRefreshing(false);
          }
        }}
      >
        Обновить список подключений
      </button>
    </section>
  );
}
