# ML integration verification, 2026-10-13

Cloud Linux checkout of `feature/bakeplan-scaffold`, based on unchanged commit `86c23e516916ac56ebac30a28a27317d841bcc5f`. Original history and authors were retained. New release: API 0.3.0 / SQLite schema 3 / `residual-ridge-100-v1-experimental`.

Executed against the integrated code before publication:

- Python 3.14.8, Node 24.19.0; locked dependencies installed.
- `python -m pytest backend/tests -q`: **152 passed**. Covers ML core, API lifecycle, per-bakery and target isolation, chronology, snapshot/JSON integrity, persisted versions and restart, bounded errors, atomic idempotency, v3 migrations, and production safety guards.
- `python -m unittest discover -s tools/tests -q`: **37 passed**. Existing offline research checks remain intact.
- `python -m ruff check backend tools`: passed.
- Frontend lint, TypeScript, production build: passed; **7 frontend tests passed**, including rendered target-isolation and legacy-data controls.
- Non-browser Playwright API retry and real API-process restart scenarios: **2 passed**. The restart scenario now also trains a model, obtains an ML forecast, confirms an ML plan, kills/restarts the API, and verifies unchanged model/forecast/plan plus idempotent training and confirmation retries.
- Independent code review rechecked the initial high-count fallback and active-catalog-history defects after correction; no remaining code-review blocker was found.
- `git diff --check`: passed.

Local full browser execution is **blocked, not passed**. The pinned Chromium archive download was invalid in this environment. Installed Chromium was then tried through the supported execution path, including sandbox escalation, but this cloud runtime denied its required local socket (`Operation not permitted`). The Playwright suite contains 12 scenarios, including four new ML UI flows. CI runs the full suite on Windows after publication; check the Actions result for the exact delivered commit. Optional `BAKEPLAN_CHROMIUM_PATH` selects an already-installed browser without changing the default CI browser.

Existing Starlette/httpx deprecation and physical-core-detection warnings did not fail the tests. No private CSV, SQLite, generated model artifact, or claimed economic result was added to Git. Synthetic checks demonstrate software behavior, not real-data accuracy. Actual bakery validation and a fresh forward shadow pilot at the real data-availability horizon remain open.

---

# Проверка каркаса BakePlan

Фактически выполнено на Penguin, Windows, Python3.14.2, Node24.13.0; октябрь2026. Автотесты используют только синтетические данные; отдельный внешний sales-only прогон выполнен приватно. Команды и зависимости находятся в README и lock-файлах.

| Проверка | Результат |
|---|---|
| Backend pytest | 27 прошли |
| Ruff backend/tools | прошёл |
| Frontend ESLint и TypeScript | прошли |
| Frontend Vitest | 4 прошли |
| Vite production build | прошёл |
| Playwright Chromium | 8 прошли, 15.8с; задержанное подтверждение, 413, non-JSON и пагинация |
| Offline/sales-only unittest | 25 прошли, включая общее ядро, обучение, temporal/zero-проверки и воспроизводимый CLI |
| npm audit root/frontend | 0 известных уязвимостей при установке |
| Startup по README | Windows: новая корневая .venv, зависимости, отдельные API/Vite процессы в E2E |
| Linux/macOS | не запускались |

Backend: отказ всего CSV при дроби/NaN/неизвестном товаре/дубликате/балансе; короткая строка, повторный заголовок и лишние поля дают422. Проверены B0=80/B1=65, исключение stockout, пропуски, нулевые остатки после списания, влияние целевого/будущего дня, партии и максимум96, активные даты, перенос остатков и отсутствие истории. Большинство stockout/unknown переводит рекомендацию в ручной режим с сохранением диагностического baseline. Закрытый целевой день по явному календарю даёт0 и запрещает ручное увеличение. Историческое воспроизведение помечено, устаревший рабочий черновик нельзя подтвердить; ранее успешный безопасный retry возвращается до проверки даты. Срез ограничен завершёнными календарными днями; фактический timestamp публикации данных не проверяется.

Транзакции: две новые подтверждающие операции с одной revision дают один успех и409; одинаковый ключ/тело при одновременном повторе дают одинаковый результат; изменённый ключ/тело и устаревшая revision конфликтуют. Изменение каталога не меняет сохранённые ограничения старого плана.

Browser: импорт → B1 → попытка90 блокируется → ручные84 → подтверждение → перезагрузка → история → CSV с synthetic. Отдельный тест действительно останавливает и снова запускает uvicorn на18001 с тем же SQLite, проверяет идентичность плана и экспорта после другого успешного импорта. База находится во временном каталоге, серверы тестов завершены; тестовые порты18000/18001/15173 не затрагивают обычный запуск на8000/5173.

Найденный при повторной интеграции сбой E2E: одновременно отображались два role=status в коротком промежутке между сохранением и загрузкой истории. Исправлено отображение статуса в UI; повторный полный прогон прошёл. Тест не ослаблялся. Обнаруженная статическим просмотром неверная подготовка второго импорта в restart-тесте заменена на валидный импорт с обязательной проверкой201.

Есть предупреждение библиотеки Starlette о будущем переходе TestClient с httpx на httpx2; текущие проверки проходят на закреплённой среде. CI добавлен отдельно; локальные результаты не означают успешного GitHub Actions до фактического запуска.

Реальная история, независимый final test качества, местный пилот, оценка экономии, Linux/macOS и публичный hosting не проверены. ML остаётся отдельным исследовательским инструментом, не источником рабочего плана. Нет обещания качества или полноты производственного расписания.

Рефакторинг: миграция сохраняет точные legacy payloads, dataset_id всех дубликатов и ссылки планов; SQLitebackup проверен, повторный startup безопасен. Мигрированный план прочитан, подтверждён и экспортирован. Конкурентные импорты возвращают один канонический ID при уникальном ключе source/schema/hash. Списки проверены как metadata-only даже без чтения payload. Изменение post-cutoff фактов не меняет items целиком (прогноз, даты, допуск, рекомендации, предупреждения). Статический независимый просмотр P0 не выявил.

Sales-only этап: сохранены все48 прежних проверок, добавлены16 регрессий (всего64). Фиксированы train/validation/test и выбор5 пищевых позиций по train-only покрытию. Приватный corrected BreadBasket CSV проверен по SHA256, реальный исследовательский CLI выполнен для двух zero-политик. В полный приватный отчёт включены общий/per-item MAE/WAPE, одинаковые paired-даты и покрытие. Он оценивает товарные строки, не физические изделия/спрос. Сырые внешние записи и прогнозы по этим записям в Git не включены; только URL/hash/provenance и синтетический пример. Инвентарные поля не создавались, API/SQLite/рабочий план не менялись. Тесты подтверждают временной cutoff, train-only selection, baseline parity, nullWAPE при нулевом знаменателе, отказ при недостатке paired-дней, синтетическую маркировку и одинаковый JSON при повторном запуске.
