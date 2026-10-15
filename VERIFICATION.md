# Проверка стабилизации MVP, 14.10.2026 (UTC+5)

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

Безопасное обновление живого демо — отдельное действие после просмотра результата: подтвердить целевой SHA и окно обновления; выполнить штатный проверенный backup SQLite, восстановить копию в изолированной среде и прогнать smoke; собрать кандидат отдельно; только затем согласованно переключить демо с сохранением прежнего образа/checkout для отката. Не читать и не пересылать `deploy.env`, не менять туннель, не копировать работающую SQLite обычным файловым копированием. В этой задаче push/merge/deploy не выполняются.

## UI-polish: проверка текущего кандидата

Изолированная ветка `feature/bakeplan-ui-polish` начинается с точного defense-head `756829475615d37d024b12e720d7cfc9338e4b68`. Материалы защиты, backend, расчёты, ML, API и SQLite сохранены. Меняется интерфейс, его регрессионные проверки и воспроизводимый UI-capture.

- Локально: 207 backend, 42 offline, 28 frontend; lint/typecheck/build и Ruff. Эти результаты не заменяют браузерный CI.
- Исходные 18 E2E-сценариев сохранены с переходами через новую навигацию, добавлены 5 сценариев: клавиатура, сохранение состояния разделов и ширины 360/768/1440. Утверждать browser-pass можно только после завершения Windows job точного SHA.
- Capture отдельно запускает нетронутый baseline и новый интерфейс на реальном API/Chromium; SHA и чистота обоих исходников, SHA harness, размеры и hashes PNG входят в manifest. Инструкции: [SCREENSHOTS](docs/SCREENSHOTS.md).
- Итоговые статусы и точный SHA проверять в [CI ветки UI-polish](https://github.com/Nurlan-byte/byte/actions?query=branch%3Afeature%2Fbakeplan-ui-polish), после интеграции также в [CI scaffold](https://github.com/Nurlan-byte/byte/actions?query=branch%3Afeature%2Fbakeplan-scaffold). Более ранний зелёный commit не является проверкой текущего.
- Ручной Windows/WSL2 и физический телефон, touch/Safari, публичный HTTPS и точность реальной пекарни остаются непроверенными. Реальные данные не использовались.


Новая ветка `feature/bakeplan-mvp-stabilization` начинается с неизменённого remote `8b8ad0195210c18b9756255db6a7136e0c026206`. В исходной версии [CI](https://github.com/Nurlan-byte/byte/actions/runs/38453855717) прошёл 152 backend, 37 offline, 8 frontend и 12 E2E проверок. Это исторический результат, не доказательство новой версии. Старый полный протокол сохранён отдельно: [HISTORICAL_VERIFICATION](docs/HISTORICAL_VERIFICATION.md).

## Полностью зелёная контрольная точка стабилизации

Для SHA `9948d911b5211e3a439c64733f61d82d4f405d49` оба запуска завершены **success**: [push CI](https://github.com/Nurlan-byte/byte/actions/runs/38462149030) и [PR CI](https://github.com/Nurlan-byte/byte/actions/runs/38462158890).

- Windows: **186 backend passed, 16 Linux-only skipped**, **42 offline**, **19 frontend**, **18 E2E** (16 browser + 2 API/process). Lint, typecheck и production build прошли.
- Linux: **30 deployment tests** и фактическая сборка двух Docker-образов, auth/assets/CSRF, ready ML, план/confirm/export, restart и verified backup/restore прошли. Пропущенные на Windows платформенные проверки входят в Linux job.
- Это автоматические проверки CI, не ручной запуск на ноутбуке пользователя и не проверка публичного HTTPS.

Следующий согласованный этап добавляет только воспроизводимые доказательства и материалы защиты. Его точный финальный SHA, CI и артефакт реальных автоматических снимков экрана фиксируются в [CI ветки материалов защиты](https://github.com/Nurlan-byte/byte/actions?query=branch%3Afeature%2Fbakeplan-defense-evidence). Более ранний зелёный SHA не заменяет повторную проверку последующих изменений.

## Подтверждённый контейнерный CI

Для `f93d5b7d749d9105c29110770743ab36ad98e3e6` [Linux job](https://github.com/Nurlan-byte/byte/actions/runs/38459541821/job/115510815667) реально прошёл: 30 deployment tests, сборка обоих образов, Caddy validate обоих режимов, auth/assets/CSRF, внутренний API, синтетический ready ML на t−2, ML-план/confirm/export, restart, backup/verify/restore в новый файл и точная сохранность данных/ключей. Это loopback Docker, не hosted HTTPS и не Docker Desktop пользователя.

Первый Windows run обнаружил только portability-дефект новых тестовых ID: 140 000 символов фикстуры попадали в `PYTEST_CURRENT_TEST` и превышали лимит Windows environment. Исправлены короткие явные IDs; сами большие входы и проверки сохранены, шесть тестов повторно пройдены локально. После исправления IDs Windows прошёл 186 backend tests (16 Linux-only пропущены и проверены отдельно), 42 offline и 18 frontend tests. Браузерный этап прошёл 15/18 и выявил три нестабильных exact-label lookup у method selects. Исправлена сама доступная разметка: label/htmlFor отдельно от select/options, также для двух import selects; все exact E2E assertions сохранены, добавлена общая регрессия (теперь 19 frontend tests). Результат повторного полного Windows/browser CI финального SHA смотрите в [CI ветки материалов защиты](https://github.com/Nurlan-byte/byte/actions?query=branch%3Afeature%2Fbakeplan-defense-evidence); предыдущие failed runs не считаются успешными.

## Текущий код

Python 3.14.8, Node 24.19.0, cloud Linux. SQLite schema 3 сохранена. Новый протокол `residual-ridge-100-v2-horizon-2-experimental`: обучающие признаки и holdout используют t−2, доступность обучающих меток ограничена первым cutoff holdout. Синтетические данные, не реальная история.

Точный SHA публикации и ссылки на его CI фиксируются в отдельном PR материалов защиты; сверяйте именно этот SHA, а не только зелёный badge ветки. До зелёного CI финального SHA браузерный и контейнерный этапы не считаются пройденными. Локальная проверка ниже относится к подготовленному к публикации дереву кода, а не к последующим изменениям.

| Этап | Фактически выполнено в cloud workspace |
|---|---|
| Backend и deployment unit/regression tests | **207 passed** (30 deployment/backup/readiness и 5 isolated replay/safety проверок включены) |
| Offline, включая frozen forward-window CLI | **42 passed**, включая 5 новых проверок forward-window |
| Python Ruff | backend, tools, scripts — passed |
| Frontend | lint, TypeScript, 19 unit/component tests, production build прошли |
| Реальный API process restart и idempotency | 2 non-browser Playwright сценария прошли; snapshot/model/forecast/plan/retry/export пережили restart |
| Browser E2E | 18 сценариев обнаружены; полный запуск передан Windows CI |
| Backup/restore | Linux tests проверяют 7 таблиц, неизменность payload/retry keys, committed WAL, manifest/hash, повреждение, отказ перезаписи и новый файл восстановления |
| Docker/Caddy/HTTPS | Docker, Podman и Caddy в этом cloud workspace отсутствуют. Dockerfile/Compose/Caddy прошли фактическую Linux CI сборку и disposable loopback smoke на SHA выше; локально в cloud workspace runtime по-прежнему отсутствует |
| Independent review | Отдельный reviewer проверил CSV, подтверждения, guards, ML chronology/common cohorts, frontend, whole-site auth/CSRF и backup/restore; code-review блокеров не осталось |
| Hosted/manual QA | Не выполнено: нет выбранного хоста/домена и операторских секретов. Второе устройство, trusted TLS, реальный restore drill и ручной просмотр остаются открыты |

Известное ограничение этого cloud runtime: Chromium ранее не смог открыть требуемый локальный socket (`Operation not permitted`). Нельзя выдавать обнаружение E2E или unit render за браузерную/ручную проверку. CI запускает 16 browser + 2 API/process сценария на Windows. Linux-only snapshot/entrypoint tests явно пропускаются на Windows и обязательно выполняются в отдельном Linux job. Изолированное CI demo использует публичные одноразовые test-fixture credentials, только loopback, затем удаляет свой отдельный проект и тома. Это не публичное размещение.

## Команды повторения из корня

```sh
.venv/bin/python -m pip install -r backend/requirements.lock -r tools/requirements.lock
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m unittest discover -s tools/tests -v
.venv/bin/python -m ruff check backend tools scripts
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
npm ci
npx playwright install chromium
npm run test:e2e
# Только Linux с Docker Engine и Compose >= 2.24.4, изолированное disposable demo:
bash scripts/test_containers.sh
```

На Windows используйте `.\.venv\Scripts\python.exe`, `npm.cmd`, `npx.cmd`; Docker snapshot-путь выполняется в Linux/container. Команды setup, запуска, доступа и восстановления находятся в [README](README.md) и [DEPLOYMENT](docs/DEPLOYMENT.md).

Существующее предупреждение Starlette/httpx и предупреждение определения физических CPU не означают падение проверок. Private-отчёт CLI на синтетике содержит 65 общих пар при указанном в [EVALUATION](docs/EVALUATION.md) периоде; это проверка процедуры, не вывод о точности. Никаких реальных CSV, SQLite, секретов или якобы доказанной экономии в Git нет.

## Материалы защиты и воспроизводимые доказательства

Стабилизация объединена пользователем через [PR #1](https://github.com/Nurlan-byte/byte/pull/1) 13.10.2026 в 20:54 UTC. Merge SHA `fc8898c3663456ebe38d906218d8afab3b14e6b5` имеет то же дерево, что `9948d911`, и отдельный зелёный [CI](https://github.com/Nurlan-byte/byte/actions/runs/38466507842). Дополнение готовится в новой ветке `feature/bakeplan-defense-evidence` от merge SHA; предыдущие ветки не переписываются.

- [DEMO](docs/DEMO.md): отдельная новая SQLite без удаления рабочей БД, точные даты и ожидаемые значения, 90-секундный сценарий, текст шести слайдов, речь, восемь вопросов жюри и порядок резервной записи.
- [EVALUATION](docs/EVALUATION.md): на синтетическом встроенном holdout B1 MAE **0,416667**, ML **1,058562** (protocol v3; v2 давал 0,767225), общих пар **60**, покрытие **60/60**. ML хуже B1 в этом примере. Это не метрика реальной пекарни. Отдельный frozen forward CLI с 65 парами не смешивается с этим holdout.
- `scripts/replay_demo.py`: реальный in-process FastAPI и временные SQLite, атомарный invalid CSV, fallback без модели, обученная модель, ручное подтверждение84, revision/idempotency и точный CSV после app lifespan reopen. Это не OS restart. Пять новых тестов проверяют в том числе неприкосновенность переданного `BAKEPLAN_DB`, отказ перезаписи и запрет запуска с отключёнными assertions.
- [SCREENSHOTS](docs/SCREENSHOTS.md): отдельный настоящий UI/API запуск и Chromium capture, laptop и mobile viewport. Основные 18 E2E не изменяются. Готовность снимков подтверждается только успешным шагом CI, полным manifest того же SHA и визуальным просмотром PNG; код съёмки сам по себе не считается полученными скриншотами.
- Финальный точный SHA, CI и ссылка на комплект снимков/JSON/CSV находятся в [CI ветки материалов защиты](https://github.com/Nurlan-byte/byte/actions?query=branch%3Afeature%2Fbakeplan-defense-evidence). В CI отчёт replay и manifest снимков содержат исходный SHA; локальные предварительные результаты дополнительно отмечают dirty tree. Скриншоты автоматические, не ручная проверка ноутбука или телефона.

## Критерий внешней приёмки

Диас записывает SHA и окружение, затем независимо проходит [manual QA](docs/manual-qa.md) и [90-секундный сценарий](docs/DEMO.md). Развёртывание считается проверенным только после whole-site auth/CSRF, импорта/обучения/фактического метода, ручного подтверждения/export, рестарта и восстановления на выбранном хосте, включая доступ с другого устройства. Независимая реальная оценка и журнал времени доступности фактов остаются отдельной задачей команды. [Windows/Docker-инструкция](docs/LOCAL_WINDOWS.md) подготовлена как локальный резерв; на ноутбуке пользователя она ещё не выполнена.
