# Фактические проверки BakePlan

## Исходная подтверждённая версия

Перед очисткой 15.10.2026 remote default branch `feature/bakeplan-scaffold` указывала на `13e3c2f433c0855c39a349c42c80dfd0bc249bec`.
[CI этого SHA](https://github.com/Nurlan-byte/byte/actions/runs/38727499192) завершён `success`: Windows `verify` и Linux `controlled-demo`.

Протокол приложения — `pooled-relative-ridge-200-v3-horizon-2-experimental`, SQLite schema 3. Исторические результаты других SHA и поколений ML сохранены в [архиве](docs/ARCHIVE.md). Синтетические проверки подтверждают исполнение программы, не точность на реальной пекарне и не экономию.

## Очистка репозитория

Область и обратимость — в [отчёте очистки](docs/REPOSITORY_CLEANUP.md). Изменяются документация, расположение двух исторических JSON-отчётов и правила ignore. Код, стили, зависимости/lock-файлы, fixtures, тесты, workflow, Docker/deploy и ML reference сохранены.

Локальный прогон 15.10.2026: Windows, Python 3.14.2, Node 24.13.0, зависимости установлены из сохранённых lock-файлов. Прогон выполнен на подготовленном дереве очистки поверх исходного SHA; изменения приложения отсутствуют. Последующие изменения требуют проверки своего commit.

| Проверка | Фактический результат |
|---|---|
| Ruff `backend tools scripts` | passed |
| Backend pytest | 219 passed, 17 skipped: Linux snapshot/entrypoint/flow и недоступное создание symlink на Windows |
| Offline unittest | 49 passed, включая frozen reference и общие когорты |
| Frontend | ESLint, TypeScript, 28 tests и production build passed |
| Playwright Chromium E2E | 23 passed, включая настоящий API process restart |
| API replay | passed; синтетический holdout B1 MAE 0,416667, ML 1,058562, N=60, покрытие 60/60; это in-process replay, не ручная QA |
| Diff/ссылки/ignore | `git diff --check` passed; 140 локальных Markdown targets/anchors и 14 ignore-кейсов проверены; архивные JSON сохраняют исходные Git blobs |
| Независимое read-only review | Astra High: блокеров не обнаружено; код/тесты/контракты и уникальные сведения сохранены |

Пропуски Windows не считаются пройденными Linux-проверками. Контейнерный сценарий и полные UI/video capture выполняет неизменённый workflow. CI итогового SHA нужно сверять в [Actions основной ветки](https://github.com/Nurlan-byte/byte/actions?query=branch%3Afeature%2Fbakeplan-scaffold); зелёный исходный SHA не подтверждает последующие изменения. Известные предупреждения Starlette/httpx и `NO_COLOR`/`FORCE_COLOR` не привели к падению тестов; зависимости ради уборки не обновлялись.

## Повторение проверок

Из корня чистого checkout после quickstart в [README](README.md):

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock -r tools/requirements.lock
.\.venv\Scripts\python.exe -m ruff check backend tools scripts
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m unittest discover -s tools/tests -v
npm.cmd ci
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
npx.cmd playwright install chromium
npm.cmd run test:e2e
```

В Linux путь Python — `.venv/bin/python`, команды — `npm`/`npx`. Полный неизменённый [workflow](.github/workflows/ci.yml) дополнительно выполняет pinned before/after capture, видеозапись и API replay. Linux job выполняет deployment tests и `bash scripts/test_containers.sh` в отдельном disposable Docker project. Эти действия не запускаются против существующей VM или её данных.

## Демо и границы доказательств

- [DEMO](docs/DEMO.md) содержит текст шести слайдов, речь, Q&A, сценарий и получение видео; [SCREENSHOTS](docs/SCREENSHOTS.md) — получение PNG и проверку manifest.
- В [исходном CI](https://github.com/Nurlan-byte/byte/actions/runs/38727499192) доступны `bakeplan-ui-evidence-13e3c2f433c0855c39a349c42c80dfd0bc249bec-1` и `bakeplan-demo-video-13e3c2f433c0855c39a349c42c80dfd0bc249bec-1` (на момент проверки не истекли). Retention — 14 дней; сохраняйте комплект для защиты отдельно и сверяйте `source_sha`, `source_dirty=false`, `capture_status=complete`.
- Ручная Windows/WSL2/phone QA, публичный HTTPS и состояние установленной VM не подтверждаются этим репозиторным прогоном. Отдельная UI-ветка не опубликована и не включается в очистку.
- Независимая будущая реальная оценка, права/доступность данных и приёмка конкретной deployment-версии остаются в [remaining tasks](docs/REMAINING_TASKS.md).
