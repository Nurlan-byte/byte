# Фактические проверки BakePlan

## Опубликованная ветка и CI, 17.10.2026

Работа опубликована в `feature/bakeplan-scaffold`. Для коммита `b258b3024f977d05bc0f17d70e4e2fc5f4d388a4` [GitHub Actions](https://github.com/Nurlan-byte/byte/actions/runs/39037626897) завершился успешно: backend, frontend, 35 браузерных сценариев, синтетический replay, скриншоты, WebM и Linux-проверка изолированного demo. Видеоманифест указывает этот SHA, чистый checkout, 95,96 секунды записи и канонический SHA синтетического CSV `a0ecee1cf940d16372d7e1f2a0fde07daf4d88792a14db95f714c8cb967f2944`. Проверяйте актуальный HEAD и новый CI после последующих изменений. Запись ниже сохраняет хронологию предыдущего локального состояния.

## Локальная проверка сценария закупки и упрощённого интерфейса, 17.10.2026

Код интерфейса и регрессий: локальный commit `e7c4932ae557ea3feacb3b9b2fadfb31e19d5d07` в ветке `feature/owner-simple-flow`. Его основа — `af38347cca1b09e09d6c9a10c64e1757e570388b`; перед ним отдельно зафиксированы изменения документации, синтетического генератора и экономики (`6d5e90b`, `1b53532`, `f8b4ccb`). Этот commit и последующие материалы **не опубликованы**: проверка разрешений отклонила `git push`. Для этого SHA нет подтверждённого удалённого CI, PR или развёртывания.

| Локальная проверка | Результат |
|---|---|
| `ruff check backend tools scripts` | passed |
| `pytest backend/tests -q` | 231 passed, 17 skipped; 1 warning |
| `python -m unittest discover -s tools/tests -v` | 49 passed |
| frontend lint, typecheck, Vitest, build | passed; 41 frontend tests |
| `npm run test:e2e -- --reporter=line` | 35 passed, Chromium на Windows; отдельная временная SQLite |
| Синтетический replay клуба | импорт → модель → прогноз → заказ → правка воды 24→36 → подтверждение → история → экспорт; отдельная локальная SQLite, результат в локальном каталоге `../prepared/cgz-synthetic-replay-final/` |

Синтетический CSV клуба имеет SHA-256 `a0ecee1cf940d16372d7e1f2a0fde07daf4d88792a14db95f714c8cb967f2944`; 91 день, 6 товаров и 546 строк. В replay использованы данные до 14.10.2026 для цели 16.10.2026. Три товара рассчитаны ML, три — B1 вследствие внутренних ограничений отбора; подробности — [CLOUD_GAME_ZONE](docs/CLOUD_GAME_ZONE.md). Сценарий проверяет воспроизводимость и процесс, но не доказывает точность или денежный эффект на реальных продажах. Скриншоты текущего чистого SHA описываются после отдельного capture; их отсутствие здесь не означает готовую видеозапись.

После изменений E2E ранее были сбои устаревших текстовых ожиданий и раскрытия нового блока настроек; затем **весь** набор 35/35 прошёл на одном локальном состоянии. В CI отдельного SHA это ещё не повторено. Предыдущий Windows CI основного коммита `af38347` завершился ошибкой одного procurement E2E (33/34 passed); новый тест сначала дожидается интерфейса и перехватывает только POST импорта, а workflow добавляет trace в артефакт при сбое. Успех локального запуска не заменяет результат CI.

## Сверка описания модели с backend

Текущая реализация [ml_core.py](backend/ml_core.py) фиксирует `ALPHA=200.0` для производственного и отдельного закупочного протоколов. Производственный `ready` не означает преимущество над B1. В закупках действует строгий внутренний per-product отбор MAE ML < MAE B1; общая оценка после отбора не является независимой оценкой всех товаров. Параметры, границы fallback и различие внутреннего holdout и внешних 14 дней сведены в [EVALUATION](docs/EVALUATION.md#фактические-параметры-отбор-и-fallback); сценарий показа — в [DEMO](docs/DEMO.md).

Это сверка документации с исходным кодом, а не новый прогон CI, новая оценка точности или подтверждение deployment. Приведённые ниже результаты относятся к указанным историческим датам и SHA; они не подтверждают текущие незакоммиченные изменения.

## Маршрут владельца: локальная проверка 15.10.2026

Ветка `feature/bakeplan-owner-ux` создана от `13e3c2f433c0855c39a349c42c80dfd0bc249bec` в отдельном worktree на Windows/PENGUIN. Исходный `C:\Dev\taraz\byte` был чистым и остался на прежней ветке. Backend, ML, схема SQLite и расчёты не менялись. Все коммиты Asylstyle и материалы защиты сохранены.

Проверенный путь: понятный пустой старт → подготовка CSV → явный импорт → дата и способ расчёта → прогноз / рекомендация / ручной выпуск → подтверждение → автоматически загружаемая история → CSV → обновление страницы и настоящий рестарт отдельного API. В браузере хранятся только ID выбранного набора и открытого плана, значения перечитываются с сервера. Несохранённый ввод не выдаётся за подтверждённый; браузер предупреждает при перезагрузке с правками. Открытие истории сохраняет страницу списка. Неизвестные старые ID дают сообщение с действием для восстановления.

Локальные результаты: ESLint, TypeScript и production build прошли; Vitest — **32 passed**, Python — **219 passed, 17 skipped** (платформенные проверки Windows/Linux), Ruff — passed; Playwright — **27 passed**. Это 23 прежних E2E и 4 новых сценария. Существующий screenshot-capture и новый owner-capture — **2 passed**. Проверены ширины 360/768/1440, клавиатурная навигация, видимый focus, связанные label, ошибки ввода, disabled-состояния и полная доступность ручных полей без горизонтальной прокрутки. Это Chromium на Windows, не физический телефон или Safari.

В новом полном браузерном тесте используется отдельная SQLite во временной папке, API `127.0.0.1:18004` и Vite `127.0.0.1:15176`; оба процесса останавливаются в `finally`. После рестарта сравниваются точный снимок плана и байты экспорта. Старый restart-тест также сохранён. Обычные E2E используют 18000/18001/15173; screenshots — 18002/15174. Существующие серверы не переиспользуются. Ubuntu-демо, его БД, контейнеры и туннель не затрагивались.

На t−2 синтетического примера по-прежнему **ML хуже B1**: MAE `1,058562` против `0,416667`, 60 общих пар. Для p1 ML `27.644832659891915`, рекомендация 24, ручное подтверждение 84. Нет подходящей истории — «Нет оценки» и явно названная ручная заготовка, не прогноз нуля. Экономия и реальная точность не заявляются. Существующий 105-секундный video-capture также прошёл (1 passed); это проверка сценария на рабочем дереве, не финальный опубликованный ролик. Для распространения нужна запись чистого выбранного commit по правилам DEMO.md.

Команды из worktree (с установленными зависимостями):

```powershell
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m pytest backend/tests -q
$env:BAKEPLAN_CHROMIUM_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
node node_modules/@playwright/test/cli.js test --output=../evidence/e2e-final
node node_modules/@playwright/test/cli.js test --config=playwright.screenshots.config.ts --output=../evidence/after
```

До/после: настоящий неизменённый baseline `13e3c2f` и текущий UI записываются `screenshots/owner-ux.spec.ts` в одинаковых состояниях: первый запуск, выбранный ещё не импортированный файл, ручной B1-план. Для baseline задаются `BAKEPLAN_CAPTURE_BASELINE=1` и `BAKEPLAN_CAPTURE_SOURCE_DIR` на исходный checkout; для after эти переменные удаляются. Вывод хранится вне `test-results`, поскольку общий Playwright-прогон очищает этот каталог. `manifest.json` фиксирует SHA, dirty status, браузер и источник. Данные только синтетические. Перед передачей сравнить manifest с `git rev-parse HEAD`; новый CI и независимая приёмка не заявляются.

Безопасное обновление живого демо — отдельное действие после просмотра результата: подтвердить целевой SHA и окно обновления; выполнить штатный проверенный backup SQLite, восстановить копию в изолированной среде и прогнать smoke; собрать кандидат отдельно; только затем согласованно переключить демо с сохранением прежнего образа/checkout для отката. Не читать и не пересылать `deploy.env`, не менять туннель, не копировать работающую SQLite обычным файловым копированием. В том UI-этапе push/merge/deploy не выполнялись.

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
