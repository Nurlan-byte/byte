# Controlled demo: сборка, доступ, хранение и восстановление

Для локального запуска на Windows через Docker Desktop/WSL2 используйте
[пошаговую инструкцию LOCAL_WINDOWS.md](LOCAL_WINDOWS.md). Она сохраняет прежнюю
native SQLite и не требует публичного размещения.

## Статус и одно решение до размещения

Это подготовка **закрытой демонстрации одной общей базы**, не публичный SaaS и не многопользовательская система. У приложения нет собственных аккаунтов, ролей и изоляции пекарен. Все допущенные пользователи видят и могут менять одни и те же данные; `bakery_id` не является проверкой доступа. Использовать только синтетические CSV либо данные с подтверждённым разрешением, подходящие для всех участников этой демонстрации.

**Решение, которое ещё нужно принять:** выбрать и одобрить конкретную отдельную Linux VM с постоянным локальным диском и DNS-именем для этой демонстрации. Провайдер, аккаунт, регион, бюджет и hostname не выбраны; ничего не куплено и не размещено. Предлагаемая стартовая конфигурация: 2 vCPU, 2 GB RAM, 20 GB persistent disk, Docker Engine и Compose >= 2.24.4. Это инженерная стартовая оценка, не измеренная вместимость. Serverless/эфемерный диск, несколько реплик и сетевой SQLite здесь не поддержаны.

Локально доступны Python 3.14.8 и Node 24.19.0. Проверены Python-тесты snapshot/restore, отказ без файла доступа и синтаксис shell-скриптов. **Docker, Podman и Caddy в среде подготовки отсутствуют:** сборка образов, Caddy validate, реальная работа прокси, HTTPS, тома и контейнерный restart/restore локально не запускались. Linux CI реально выполнил контейнерный сценарий на `f93d5b7d749d9105c29110770743ab36ad98e3e6`: [успешный job](https://github.com/Nurlan-byte/byte/actions/runs/38459541821/job/115510815667). Для последующих изменений проверять CI конкретного commit. Реальный host, DNS/сертификаты и ручная браузерная приёмка остаются открытыми до выполнения на выбранной VM.

## Что подготовлено

- `Dockerfile`, target `api`: Python 3.14.8, полный pinned `backend/requirements.lock`, непривилегированный UID/GID 10001, один Uvicorn worker, без reload.
- `Dockerfile`, target `gateway`: `npm ci` по `frontend/package-lock.json`, TypeScript/Vite production build на Node 24.19.0, статика в Caddy.
- `deploy/compose.yaml`: только `127.0.0.1:8080` наружу хоста. API не публикует порт 8000, живёт в отдельной internal-сети. Caddy маршрутизирует `/api` и `/api/*` без удаления префикса. React использует относительный `/api`, поэтому отдельный backend URL и production CORS не нужны.
- `deploy/compose.https.yaml`: явный opt-in для 80/443, Caddy automatic HTTPS, HSTS и persistent certificate volumes. Compose `!override` заменяет loopback mapping, не добавляет второй незашифрованный вход.
- Все страницы, built JS/CSS, `demo.csv`, API, экспорт, неизвестные пути и health проходят одну Basic Auth-проверку **до** маршрутизации. Нет пароля по умолчанию. Нет файла или он пустой/нечитаемый: gateway отказывается запускаться. Сам API рассчитывает на эту границу и не должен быть опубликован напрямую.
- Небезопасные методы требуют `X-BakePlan-Request: 1`; фронтенд ставит его на POST. Это защита от simple cross-origin запросов с сохранённой браузером Basic Auth. Cross-origin preflight без авторизации получает 401; `Sec-Fetch-Site: cross-site` на записи дополнительно даёт 403. CORS сам по себе не является CSRF-защитой.
- Именованные тома `sqlite_data`, `backup_data`, `caddy_data`, `caddy_config`; база `/data/${BAKEPLAN_DB_FILE}`. Они переживают restart/recreate обычных контейнеров. `down --volumes`, удаление VM/диска или pruning томов уничтожает данные: не использовать на демонстрационном хосте.
- У health endpoint read-only открытие SQLite и проверка основных таблиц; отсутствие БД/таблиц даёт 503 и не создаёт пустую БД. Это readiness, не полный аудит данных. Контейнерные логи ограничены по размеру; HTTP access-log с URL/данными отключён.

Basic Auth годится лишь для небольшой доверенной группы. Не использовать общий тестовый пароль из CI, не отправлять настоящие пароли в git/chat/аргументы команд. Нет MFA, logout/session management, rate limiting, audit по пользователю или восстановления учётной записи. Если эти свойства нужны, сначала добавить полноценную аутентификацию/авторизацию; не открывать этот demo широкой аудитории.

## 1. Подготовка оператором, без публикации

Команды ниже для **Linux host и Linux containers**, из корня чистого проверенного checkout. Snapshot CLI использует POSIX permissions, atomic hardlinks и directory fsync; Windows PowerShell quickstart основного приложения не означает поддержку этих host-команд на Windows. На Windows запускать deployment/snapshot внутри Linux Docker/WSL. Директория snapshot/restore должна быть на обычной локальной файловой системе с поддержкой hardlink/fsync, не object storage/NFS.

1. Установить Docker/Compose из официального источника, проверить `docker compose version` (>= 2.24.4) и `docker info`. Перед установкой/оплатой/изменением firewall получить необходимое разрешение владельца.
2. Создать приватную директорию **вне checkout** и скопировать шаблон:

   ```sh
   install -d -m 700 /srv/bakeplan/private
   cp deploy/.env.example /srv/bakeplan/private/deploy.env
   chmod 600 /srv/bakeplan/private/deploy.env
   ```

3. Оператор сам выбирает сильный уникальный пароль и вводит его в интерактивный официальный инструмент (не в аргументы):

   ```sh
   docker run --rm -it --entrypoint caddy caddy:2.10.2-alpine hash-password
   ```

   В `/srv/bakeplan/private/demo_users` оператор сохраняет строки `username bcrypt_hash`, по строке на допущенного участника. Например, структура строки — `chosen_user <OUTPUT_OF_HASH_PASSWORD>`, но angle-bracket placeholder надо заменить настоящим хешем. Не добавлять `basic_auth {}`: блок уже в Caddyfile. Это provisioning настоящего доступа выполняет владелец, а не CI.

   ```sh
   chmod 644 /srv/bakeplan/private/demo_users
   ```

   Внешняя директория остаётся `0700`; файл с хешами должен быть читаем после bind-mount Compose, потому что Caddy лишён `DAC_OVERRIDE`. Compose file-secrets не гарантируют перемаппинг `uid/mode` для host-файла. Не класть этот файл в общедоступную директорию. Пароль/хеш не попадает в образ или переменные окружения.

4. В `deploy.env` задать абсолютный `BAKEPLAN_AUTH_FILE=/srv/bakeplan/private/demo_users` и уникальный `BAKEPLAN_RELEASE` для проверенного commit. `BAKEPLAN_DB_FILE=bakeplan.sqlite3` при первом запуске. Секреты, SQLite и backups исключены из Docker context через `.dockerignore`.
5. Для hosted release разрешить и зафиксировать registry digest базовых образов. Шаблон имеет точные version tags, но теги изменяемы, поэтому сами по себе не обещают побитовую воспроизводимость:

   ```sh
   docker pull python:3.14.8-slim-bookworm
   docker pull node:24.19.0-bookworm-slim
   docker pull caddy:2.10.2-alpine
   docker image inspect --format '{{index .RepoDigests 0}}' python:3.14.8-slim-bookworm
   docker image inspect --format '{{index .RepoDigests 0}}' node:24.19.0-bookworm-slim
   docker image inspect --format '{{index .RepoDigests 0}}' caddy:2.10.2-alpine
   ```

   Сохранить реальные `repository@sha256:...` в `PYTHON_IMAGE`, `NODE_IMAGE`, `CADDY_IMAGE` в приватном release env. Не подставлять выдуманные digest. Зафиксировать также commit, архитектуру, Compose version и IDs конечных образов. Перед обновлением провести security scan/проверки и заменить pinned digest осознанно. Изменения пакетов и обновления базовых образов проверять отдельно; lock-файлы не заменяют этот контроль.

## 2. Loopback rehearsal

```sh
ENV_FILE=/srv/bakeplan/private/deploy.env
compose() { docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml "$@"; }
compose config --quiet
compose build --pull
compose run --rm --no-deps gateway caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
compose up -d --wait --wait-timeout 90
compose ps
python3 -m scripts.check_demo http://127.0.0.1:8080
```

`check_demo` интерактивно спрашивает credentials, не сохраняет их, проверяет 401 без авторизации (включая реальные JS/CSS), authenticated health/catalog, запрет CSRF и отсутствие маршрутизации API на HTML. Он отклоняет remote plain HTTP и редиректы с credentials. Его write probes направлены на отсутствующий `/api/__deployment_probe__` и не создают данные. Это не замена браузерной проверке.

Открыть `http://127.0.0.1:8080` на самом хосте либо через отдельно разрешённый SSH tunnel. Никогда не публиковать этот HTTP mapping на `0.0.0.0` и не передавать Basic Auth по внешнему HTTP. Для внешнего доступа применять HTTPS-вариант ниже.

Проверить в браузере синтетический сценарий из [DEMO.md](DEMO.md): import → train → forecast с честным lag/cutoff → draft → confirm → export → history. Все POST проходят с `X-BakePlan-Request: 1`. Недопущенному пользователю не должны быть доступны ни API, ни CSV/JS/assets.

## 3. HTTPS на одобренной VM

После выбора host и согласования публикации:

1. Направить DNS A/AAAA на выбранную VM. Не оставлять неверную AAAA-запись. Разрешить только необходимые входящие порты 80/443 и отдельно ограниченный админский доступ; API 8000 не открывать. Проверять фактические Docker-published ports, а не полагаться только на host firewall.
2. В `deploy.env` задать **только hostname** в `BAKEPLAN_DOMAIN`, без scheme/path/port, и согласованный operator email в `BAKEPLAN_ACME_EMAIL` (используется certificate authority). Caddy хранит account keys/certificates в `caddy_data`. Не удалять этот том при релизе.
3. Выбрать HTTPS-конфигурацию:

   ```sh
   compose() { docker compose --env-file "$ENV_FILE" -f deploy/compose.yaml -f deploy/compose.https.yaml "$@"; }
   compose config --quiet
   compose run --rm --no-deps gateway caddy validate --config /etc/caddy/Caddyfile.https --adapter caddyfile
   compose up -d --wait --wait-timeout 90
   python3 -m scripts.check_demo https://YOUR_APPROVED_HOSTNAME
   ```

4. Не обходить ошибки сертификатов и не применять `curl -k`. Снаружи проверить trusted HTTPS, HTTP→HTTPS redirect без содержимого приложения, весь auth/asset/API smoke, недоступность `host:8000`, browser workflow и restore drill. Проверить disk/volume retention и владельца backup-процесса. Сам факт `compose up` не доказывает успешное получение сертификата.

Локальный gateway health в HTTPS-режиме проверяет только процесс на **внутриконтейнерном loopback** `127.0.0.1:8081/ready`; этот listener не публикуется и не содержит приложение/данные. Внешний authenticated health отдельно проходит обычную защиту. Нужен внешний монитор TLS/health и сроков/ошибок renewal; не считать внутренний process health проверкой сертификата.

## 4. Backup: консистентный snapshot, не `cp` живой базы

Linux CLI `python -m scripts.sqlite_snapshot` использует SQLite backup API, включая committed WAL. Получается пара `NAME.sqlite3` + `NAME.sqlite3.manifest.json`. Manifest содержит SHA-256, версию схемы и counts всех семи таблиц. Проверяются SQLite integrity, schema v3, состав таблиц/колонок, ссылки и hashes model artifacts. Существующие snapshots не перезаписываются; невалидные/неполные пары отклоняются.

```sh
# Уникальное имя: повтор с тем же именем намеренно откажет.
SNAP="demo-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
compose exec -T api python -m scripts.sqlite_snapshot backup --output "/backups/$SNAP"
compose exec -T api python -m scripts.sqlite_snapshot verify "/backups/$SNAP"
```

`backup_data` находится на той же VM: это **не защита от потери host/disk**. Оператор копирует оба файла в заранее разрешённое приватное, шифрованное off-host хранилище и проверяет восстановление. Не отправлять backup произвольному сервису: он содержит все импортированные данные. Например, для разрешённой локальной staging-папки:

```sh
install -d -m 700 /srv/bakeplan/private/backup-export
compose cp "api:/backups/$SNAP" "/srv/bakeplan/private/backup-export/$SNAP"
compose cp "api:/backups/$SNAP.manifest.json" "/srv/bakeplan/private/backup-export/$SNAP.manifest.json"
chmod 600 /srv/bakeplan/private/backup-export/*
```

Manifest обнаруживает повреждение/несогласованность, но **не является цифровой подписью**: злоумышленник, имеющий доступ к обоим файлам, может их заменить. Защищать и контролировать доступ к backup-хранилищу. Вставка raw WAL-файлов, чужой/новой схемы, SQL dump, pickle и самостоятельные изменения snapshots не поддерживаются. Весь backup/verify snapshot должен быть неизменяем в процессе проверки.

До принятия расписания нет обещанного RPO/RTO. Для небольшого demo ориентир: backup перед каждым релизом и после важных сессий, минимум ежедневная проверенная off-host копия, 7 дневных и 4 недельных копии, ежемесячный restore drill. Это план для согласования с владельцем, не уже настроенная автоматизация. Не удалять старые копии до проверки новых.

## 5. Verified restore без перезаписи исходной БД

Сначала сохранить текущий snapshot, если исходная БД читается. Затем остановить **gateway и API**. Restore всегда создаёт новое имя; старый database/sidecars сохраняются для расследования/rollback. CLI отказывает без `--app-stopped`, при существующем destination/symlink/sidecar и при неверном hash/schema/integrity. Флаг — подтверждение оператора, а не автоматический детектор остановленного сервиса.

```sh
compose stop gateway api
compose run --rm --no-deps api python -m scripts.sqlite_snapshot verify /backups/CHOSEN.sqlite3
compose run --rm --no-deps api python -m scripts.sqlite_snapshot restore /backups/CHOSEN.sqlite3 --database /data/restored-UNIQUE.sqlite3 --app-stopped
```

Выбрать уникальное новое имя, затем изменить `BAKEPLAN_DB_FILE=restored-UNIQUE.sqlite3` в том же `deploy.env` и запустить:

```sh
compose up -d --wait --wait-timeout 90
# Использовать URL выбранного режима:
python3 -m scripts.check_demo http://127.0.0.1:8080
```

В HTTPS-режиме вместо loopback URL использовать свой HTTPS origin. Вручную сравнить до/после datasets, модели и их hashes/metadata, plans, ревизии, CSV export и поведение прежних idempotency keys import/train/confirm. Не заменять это только числом строк. Не удалять исходную БД после одной зелёной health-проверки.

Для rollback остановить оба сервиса, вернуть прошлый `BAKEPLAN_DB_FILE` и прежний проверенный image/release, запустить и повторить проверки. Старый бинарник с новой схемой не запускать; миграционные `.pre-v*.bak` не заменяют эту backup-пару и требуют отдельного совместимого восстановления. Для rollback после будущих миграций использовать заранее проверенный snapshot и соответствующий release.

## 6. Restart, обновления и диагностика

- `restart: unless-stopped` поднимает завершившийся процесс/контейнер. **Docker не перезапускает контейнер только из-за `unhealthy`**. Нужен оператор/разрешённый монитор, который проверяет health, диск, логи, сертификат и причину, затем решает о restart. Автоheal с Docker socket не добавлен.
- `compose logs --tail=100 api gateway` и `compose ps` помогают увидеть ошибку. Не публиковать логи/БД: они могут содержать операционный контекст. `compose restart api` проверять последующим health и чтением сохранённых данных.
- Перед релизом: backup + verify → release/lock/image checks → остановка/обновление → `up --wait` → auth/health/browser checks → новый проверенный snapshot. Не масштабировать API; явный worker count = 1.
- CPU/RAM limits — ограничения demo, не benchmark. Большое обучение или заполненный диск может дать 503/timeout. Не ослаблять лимиты/валидацию ради зелёной проверки; измерить и выбрать подходящий host или уменьшить допустимый input.

## 7. Проверки и границы доказательств

```sh
# Linux; package/module запуск сохраняет правильный import path.
python -m pytest -q backend/tests/test_deployment.py
python -m ruff check scripts backend/app.py backend/tests/test_deployment.py
bash -n scripts/test_containers.sh
sh -n deploy/gateway-entrypoint.sh

# Нужен реальный локальный Docker/Compose. Только disposable test project.
bash scripts/test_containers.sh
```

Контейнерный скрипт создаёт собственный уникальный Compose project и временный **публично известный test-only** password/hash, доступный лишь через loopback. Проверяет missing-secret fail-closed, сборку образов, обе Caddy-конфигурации, 401 для всего сайта/assets/API, CSRF guards и отсутствие опубликованного API; затем синтетический import → train ready → ML forecast t−2 → ML plan → confirm/export, restart, backup/verify, restore в новый файл, повторное чтение и retry keys. Trap удаляет только его disposable volumes и временные файлы. Не применять этот скрипт к операторскому demo и не переносить его тестовый пароль на host.

`test_deployment.py` проверяет сохранность всех таблиц/полей, models/hashes, revisions/keys, committed WAL, отдельный процесс после restore, invalid backups, отсутствие overwrite, readiness missing-table/DB и безопасные URL. Linux-specific snapshot/entrypoint cases явно пропускаются на Windows и выполняются отдельным Linux CI job. Текстовые assertions Compose/Caddy — только regression guards; они не заменяют работающий runtime.

CI loopback HTTP не проверяет публичный DNS, issuance/renewal trusted TLS, provider persistence, внешний firewall, off-host backup, browser acceptance или user authorization. Эти пункты нельзя отмечать выполненными по наличию файлов или успешному unit test.

Официальные справочники: [Caddy Basic Auth](https://caddyserver.com/docs/caddyfile/directives/basic_auth), [Caddy route ordering](https://caddyserver.com/docs/caddyfile/directives/route), [Caddy SPA/API patterns](https://caddyserver.com/docs/caddyfile/patterns), [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https), [Compose startup/health](https://docs.docker.com/compose/how-tos/startup-order/), [Compose override merge](https://docs.docker.com/reference/compose-file/merge/), [Python SQLite backup API](https://docs.python.org/3.14/library/sqlite3.html#sqlite3.Connection.backup).
