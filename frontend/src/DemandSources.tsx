import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Dataset } from "./types";
import type { ImportProps } from "./ImportSection";
import {
  DailyConnections,
  type DailyForecast,
  type PosConnectionSummary,
} from "./DailyPos";

type Location = { id: string; name: string };
type ImportResult =
  | { status: "choose_location"; locations: Location[] }
  | {
      status: "connected";
      connection: PosConnectionSummary;
      dataset: Dataset | null;
      forecast: DailyForecast | null;
      context_warnings: string[];
    }
  | {
      status: "imported";
      dataset: Dataset;
      location: Location;
      context_warnings: string[];
    };
export type AdoptDataset = (
  dataset: Dataset,
  warnings: string[],
  fromPos: boolean,
  connection?: { summary: PosConnectionSummary; forecast: DailyForecast },
) => Promise<void>;

function CityField({
  value,
  onChange,
  auto,
  busy,
}: {
  value: string;
  onChange: (city: string) => void;
  auto?: boolean;
  busy: boolean;
}) {
  const [cities, setCities] = useState<Location[]>([]);
  useEffect(() => {
    let active = true;
    void api<Location[]>("/context/cities")
      .then((list) => {
        if (active) setCities(list);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  return (
    <label className="control-field">
      Город для погоды
      <select
        value={value}
        disabled={busy}
        onChange={(e) => onChange(e.target.value)}
      >
        {auto && <option value="">Определить по торговой точке</option>}
        <option value="none">Календарь Казахстана без погоды</option>
        {cities.map((city) => (
          <option value={city.id} key={city.id}>
            {city.name}
          </option>
        ))}
        <option value="weekday_only">Другая страна · только дни недели</option>
      </select>
    </label>
  );
}

function KeyForm({
  busy,
  run,
  adopt,
  dailyEnabled,
  onConnected,
}: {
  busy: boolean;
  run: ImportProps["run"];
  adopt: AdoptDataset;
  dailyEnabled: boolean;
  onConnected: (connection: PosConnectionSummary) => void;
}) {
  const [provider, setProvider] = useState("poster");
  const [key, setKey] = useState("");
  const [locations, setLocations] = useState<Location[]>([]);
  const [location, setLocation] = useState("");
  const [city, setCity] = useState("");
  const [days, setDays] = useState("56");
  const [daily, setDaily] = useState(dailyEnabled);
  const [failure, setFailure] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const readVersion = useRef(0);
  function updateKey(value: string) {
    ++readVersion.current;
    setKey(value);
    setLocations([]);
    setLocation("");
    setFailure("");
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void run(async () => {
          setFailure("");
          let result: ImportResult;
          try {
            result = await api<ImportResult>(
              daily ? "/pos/connections" : "/pos/import",
              {
                provider,
                api_key: key.trim(),
                days: Number(days),
                location_id: location || undefined,
                city: city || undefined,
              },
            );
          } catch (err) {
            setFailure(
              err instanceof Error
                ? err.message
                : "Не удалось прочитать продажи.",
            );
            return;
          }
          if (result.status === "choose_location") {
            setLocations(result.locations);
            setLocation("");
            return;
          }
          setKey("");
          if (file.current) file.current.value = "";
          if (result.status === "connected") {
            onConnected(result.connection);
            if (result.dataset && result.forecast) {
              await adopt(result.dataset, result.context_warnings, true, {
                summary: result.connection,
                forecast: result.forecast,
              });
            }
            return;
          }
          await adopt(result.dataset, result.context_warnings, true);
        });
      }}
    >
      <div className="controls">
        <label className="control-field">
          Кассовая система
          <select
            value={provider}
            disabled={busy}
            onChange={(e) => {
              setProvider(e.target.value);
              updateKey("");
              if (file.current) file.current.value = "";
            }}
          >
            <option value="poster">Poster POS</option>
            <option value="moysklad">МойСклад</option>
          </select>
        </label>
        <label className="control-field">
          Ключ API кассы
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
            maxLength={1024}
            required
            value={key}
            onChange={(e) => updateKey(e.target.value)}
          />
        </label>
      </div>
      {dailyEnabled && (
        <label className="daily-choice">
          <input
            type="checkbox"
            checked={daily}
            disabled={busy}
            onChange={(e) => setDaily(e.target.checked)}
          />
          Обновлять продажи и прогноз каждый день
        </label>
      )}
      <details>
        <summary>Файл ключа, город и период продаж</summary>
        <label className="control-field">
          Текстовый файл с ключом
          <input
            ref={file}
            type="file"
            accept=".txt,text/plain"
            disabled={busy}
            onChange={async (e) => {
              const chosen = e.target.files?.[0];
              if (!chosen) return;
              const version = ++readVersion.current;
              if (chosen.size > 4096) {
                updateKey("");
                setFailure("Выберите текстовый файл до 4 КБ с одним ключом.");
                return;
              }
              try {
                const content = (await chosen.text()).trim();
                if (version !== readVersion.current) return;
                if (!/^[!-~]{1,1024}$/.test(content)) {
                  updateKey("");
                  setFailure(
                    "В файле должен быть один ключ до 1024 символов без пробелов.",
                  );
                  return;
                }
                updateKey(content);
              } catch {
                if (version !== readVersion.current) return;
                updateKey("");
                setFailure("Не удалось прочитать файл ключа.");
              }
            }}
          />
        </label>
        <CityField value={city} onChange={setCity} auto busy={busy} />
        <label className="control-field">
          Сколько завершённых дней загрузить
          <input
            type="number"
            min={1}
            max={120}
            step={1}
            required
            disabled={busy}
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </label>
      </details>
      {locations.length > 0 && (
        <label className="control-field">
          Торговая точка
          <select
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            disabled={busy}
            required
          >
            <option value="">Выберите одну точку</option>
            {locations.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {failure && (
        <p role="alert" className="field-error">
          {failure}
        </p>
      )}
      <p className="muted">
        {daily
          ? "BakePlan сохранит ключ на сервере в зашифрованном виде. Обновление проходит каждый день в 05:00 по времени Казахстана, даже когда браузер закрыт."
          : "Ключ используется для одного импорта. Для следующей загрузки введите его снова."}{" "}
        Если доступно несколько точек, появится выбор.
      </p>
      <button
        disabled={busy || !key.trim() || (locations.length > 0 && !location)}
      >
        {daily
          ? "Подключить ежедневное обновление"
          : "Загрузить продажи и получить прогноз"}
      </button>
    </form>
  );
}

type PosStatus = {
  enabled: boolean;
  daily_enabled?: boolean;
  daily_configured?: boolean;
};

function ConnectionPanel({
  busy,
  run,
  adopt,
}: {
  busy: boolean;
  run: ImportProps["run"];
  adopt: AdoptDataset;
}) {
  const [status, setStatus] = useState<PosStatus | null>(null);
  const [connections, setConnections] = useState<PosConnectionSummary[]>([]);
  const [issue, setIssue] = useState("");
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const available = await api<PosStatus>("/pos/status");
        if (!active) return;
        setStatus(available);
        if (
          available.enabled &&
          (available.daily_enabled || available.daily_configured)
        ) {
          const rows = await api<PosConnectionSummary[]>("/pos/connections");
          if (active) setConnections(rows);
        }
      } catch (error) {
        if (active)
          setIssue(
            error instanceof Error
              ? error.message
              : "Не удалось проверить подключения.",
          );
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);
  if (loading) return <p>Проверяем подключения к кассам…</p>;
  if (!status) return <p role="alert">{issue} Откройте форму ещё раз.</p>;
  if (!status.enabled)
    return (
      <p>
        Ввод ключей выключен на этом сервере. На рабочем сервере администратор
        может включить подключение к кассам. Здесь доступны CSV и примеры.
      </p>
    );
  const form = (
    <KeyForm
      busy={busy}
      run={run}
      adopt={adopt}
      dailyEnabled={!!status.daily_enabled}
      onConnected={(saved) =>
        setConnections((rows) => [
          saved,
          ...rows.filter((row) => row.id !== saved.id),
        ])
      }
    />
  );
  return (
    <>
      {issue && (
        <p role="alert" className="field-error">
          {issue}
        </p>
      )}
      {status.daily_configured && !status.daily_enabled && (
        <p className="status-note">
          Хранилище ключей временно недоступно. Проверьте настройку ключа
          шифрования на сервере. Доступен разовый импорт.
        </p>
      )}
      {(connections.length > 0 ||
        (issue && (status.daily_enabled || status.daily_configured))) && (
        <DailyConnections
          connections={connections}
          dailyAvailable={!!status.daily_enabled}
          adopt={adopt}
          busy={busy}
          run={run}
          refresh={async () => {
            try {
              const rows =
                await api<PosConnectionSummary[]>("/pos/connections");
              setConnections(rows);
              setIssue("");
            } catch (error) {
              setIssue(
                error instanceof Error
                  ? error.message
                  : "Не удалось прочитать подключения.",
              );
            }
          }}
        />
      )}
      {connections.length > 0 ? (
        <details
          className="additional-connection"
          onToggle={(event) => setAdding(event.currentTarget.open)}
        >
          <summary>Подключить ещё одну кассу или заменить ключ</summary>
          {adding && form}
        </details>
      ) : (
        form
      )}
    </>
  );
}

export function PosConnection({
  busy,
  run,
  adopt,
  active = true,
}: {
  busy: boolean;
  run: ImportProps["run"];
  adopt?: AdoptDataset;
  active?: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!adopt) return null;
  return (
    <details
      className="demand-source"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>Подключить кассу по ключу API</summary>
      {open && active && (
        <ConnectionPanel busy={busy} run={run} adopt={adopt} />
      )}
    </details>
  );
}

export function ContextControl({
  dataset,
  busy,
  run,
  adopt,
}: {
  dataset: Dataset;
  busy: boolean;
  run: ImportProps["run"];
  adopt?: AdoptDataset;
}) {
  const [open, setOpen] = useState(false);
  const [city, setCity] = useState("none");
  if (!adopt || !dataset.metadata_declared) return null;
  return (
    <details
      className="demand-source"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>Календарь и погода для прогноза</summary>
      {open && (
        <>
          <p>
            Добавьте календарь для пятидневной недели или выберите город.
            BakePlan проверит модель на прошлых продажах. График работы точки
            задаётся отдельно.
          </p>
          <CityField value={city} onChange={setCity} busy={busy} />
          <button
            className="secondary"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const result = await api<{
                  dataset: Dataset;
                  warnings: string[];
                }>(`/datasets/${dataset.id}/demand-context`, { city });
                await adopt(result.dataset, result.warnings, false);
              })
            }
          >
            Применить и рассчитать прогноз
          </button>
        </>
      )}
    </details>
  );
}
