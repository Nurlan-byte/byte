import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Dataset } from "./types";
import type { ImportProps } from "./ImportSection";

type Location = { id: string; name: string };
type ImportResult =
  | { status: "choose_location"; locations: Location[] }
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
}: {
  busy: boolean;
  run: ImportProps["run"];
  adopt: AdoptDataset;
}) {
  const [provider, setProvider] = useState("poster");
  const [key, setKey] = useState("");
  const [locations, setLocations] = useState<Location[]>([]);
  const [location, setLocation] = useState("");
  const [city, setCity] = useState("");
  const [days, setDays] = useState("56");
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [failure, setFailure] = useState("");
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let active = true;
    void api<{ enabled: boolean }>("/pos/status")
      .then((status) => {
        if (active) setEnabled(status.enabled);
      })
      .catch(() => {
        if (active)
          setFailure(
            "Не удалось проверить подключение к серверу. Откройте форму ещё раз.",
          );
      });
    return () => {
      active = false;
    };
  }, []);
  function updateKey(value: string) {
    setKey(value);
    setLocations([]);
    setLocation("");
    setFailure("");
  }
  if (failure && enabled === null) return <p role="alert">{failure}</p>;
  if (enabled === null) return <p>Проверяем доступ к интеграциям…</p>;
  if (!enabled)
    return (
      <p>
        Ввод ключей выключен на этом сервере. На рабочем сервере администратор
        может включить подключение к кассам. Здесь доступны CSV и примеры.
      </p>
    );
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void run(async () => {
          setFailure("");
          let result: ImportResult;
          try {
            result = await api<ImportResult>("/pos/import", {
              provider,
              api_key: key.trim(),
              days: Number(days),
              location_id: location || undefined,
              city: city || undefined,
            });
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
              if (chosen.size > 4096) {
                updateKey("");
                setFailure("Выберите текстовый файл до 4 КБ с одним ключом.");
                return;
              }
              try {
                const content = (await chosen.text()).trim();
                if (!/^[!-~]{1,1024}$/.test(content)) {
                  updateKey("");
                  setFailure(
                    "В файле должен быть один ключ до 1024 символов без пробелов.",
                  );
                  return;
                }
                updateKey(content);
              } catch {
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
        Ключ нужен один раз для этого импорта. BakePlan сохранит дневные продажи
        и рассчитает прогноз. Для нескольких точек появится выбор. По умолчанию
        используется календарь Казахстана.
      </p>
      <button
        disabled={busy || !key.trim() || (locations.length > 0 && !location)}
      >
        Загрузить продажи и получить прогноз
      </button>
    </form>
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
      {open && active && <KeyForm busy={busy} run={run} adopt={adopt} />}
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
