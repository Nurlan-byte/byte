# Проверка стабилизации MVP, 14.10.2026 (UTC+5)

Новая ветка `feature/bakeplan-mvp-stabilization` начинается с неизменённого remote `8b8ad0195210c18b9756255db6a7136e0c026206`. В исходной версии [CI](https://github.com/Nurlan-byte/byte/actions/runs/38453855717) прошёл 152 backend, 37 offline, 8 frontend и 12 E2E проверок. Это исторический результат, не доказательство новой версии. Старый полный протокол сохранён отдельно: [HISTORICAL_VERIFICATION](docs/HISTORICAL_VERIFICATION.md).

## Подтверждённый контейнерный CI

Для `f93d5b7d749d9105c29110770743ab36ad98e3e6` [Linux job](https://github.com/Nurlan-byte/byte/actions/runs/38459541821/job/115510815667) реально прошёл: 30 deployment tests, сборка обоих образов, Caddy validate обоих режимов, auth/assets/CSRF, внутренний API, синтетический ready ML на t−2, ML-план/confirm/export, restart, backup/verify/restore в новый файл и точная сохранность данных/ключей. Это loopback Docker, не hosted HTTPS и не Docker Desktop пользователя.

Первый Windows run обнаружил только portability-дефект новых тестовых ID: 140 000 символов фикстуры попадали в `PYTEST_CURRENT_TEST` и превышали лимит Windows environment. Исправлены короткие явные IDs; сами большие входы и проверки сохранены, шесть тестов повторно пройдены локально. Результат повторного полного Windows/browser CI для финального SHA смотрите в draft PR; предыдущий failed run не считается успешным.

## Текущий код

Python 3.14.8, Node 24.19.0, cloud Linux. SQLite schema 3 сохранена. Новый протокол `residual-ridge-100-v2-horizon-2-experimental`: обучающие признаки и holdout используют t−2, доступность обучающих меток ограничена первым cutoff holdout. Синтетические данные, не реальная история.

Точный SHA публикации и ссылки на его CI фиксируются в draft PR; сверяйте именно этот SHA, а не только зелёный badge ветки. До зелёного CI финального SHA браузерный и контейнерный этапы не считаются пройденными. Локальная проверка ниже относится к подготовленному к публикации дереву кода, а не к последующим изменениям.

| Этап | Фактически выполнено в cloud workspace |
|---|---|
| Backend и deployment unit/regression tests | **202 passed** (включая 30 deployment/backup/readiness проверок) |
| Offline, включая frozen forward-window CLI | **42 passed**, включая 5 новых проверок forward-window |
| Python Ruff | backend, tools, scripts — passed |
| Frontend | lint, TypeScript, 18 unit/component tests, production build прошли |
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

## Критерий внешней приёмки

Диас записывает SHA и окружение, затем независимо проходит [manual QA](docs/manual-qa.md) и [90-секундный сценарий](docs/DEMO.md). Развёртывание считается проверенным только после whole-site auth/CSRF, импорта/обучения/фактического метода, ручного подтверждения/export, рестарта и восстановления на выбранном хосте, включая доступ с другого устройства. Независимая реальная оценка и журнал времени доступности фактов остаются отдельной задачей команды. [Windows/Docker-инструкция](docs/LOCAL_WINDOWS.md) подготовлена как локальный резерв; на ноутбуке пользователя она ещё не выполнена.
