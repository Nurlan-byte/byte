# Запуск на Windows: локальная версия для защиты

**Самый быстрый запасной вариант** — уже знакомый запуск Python 3.14 + Node 24 из [README](../README.md#быстрый-запуск). Ниже — отдельный Docker-вариант: одна локальная ссылка `http://127.0.0.1:8080`, собранный React, FastAPI и постоянная SQLite. Он не публикует приложение в интернет и не создаёт платных ресурсов.

**Честный статус:** приложение и snapshot/restore проверены в cloud Linux; Docker Desktop/WSL2 на вашем Windows-компьютере ещё не проверены. В среде подготовки нет Docker runtime. На SHA `9948d911` [полный CI](https://github.com/Nurlan-byte/byte/actions/runs/38462149030) прошёл Windows unit/browser и Linux container build/ML/restart/backup/restore. Он не заменяет ручную проверку Docker Desktop на вашем ноутбуке. Финальную более позднюю версию сверяйте по SHA и CI в [CI ветки UI-polish](https://github.com/Nurlan-byte/byte/actions?query=branch%3Afeature%2Fbakeplan-ui-polish). Не удаляйте прежнюю рабочую папку и её SQLite, пока не пройдёте сценарий ниже.

Краткий порядок действий утром: [MORNING_CHECKLIST](MORNING_CHECKLIST.md).

## 1. Один раз: Docker Desktop и WSL2

В Windows PowerShell выполните только проверки:

```powershell
wsl --version
wsl -l -v
```

Нужны поддерживаемая версия Windows, WSL >= 2.1.5, Ubuntu в режиме **VERSION 2**, аппаратная виртуализация и минимум 8 GB RAM. Точные требования и официальный установщик: [Docker Desktop для Windows](https://docs.docker.com/desktop/setup/install/windows-install/). Для личного/учебного использования Docker Desktop бесплатен; корпоративные/государственные условия могут требовать подписку. Не оформляйте её автоматически; при неподходящей лицензии используйте native-запуск из README. Соглашение установщика принимает владелец компьютера.

Если WSL/Ubuntu нет, владелец устанавливает их по [официальной инструкции Microsoft](https://learn.microsoft.com/en-us/windows/wsl/install), при необходимости перезагружает компьютер. В ней используется `wsl --install`; для существующей установки предусмотрено `wsl --update`. Не переустанавливайте и не удаляйте существующий WSL-дистрибутив с файлами.

Запустите Docker Desktop. Выберите **Linux containers**, WSL2 engine и включите Ubuntu в **Settings → Resources → WSL Integration** ([инструкция Docker](https://docs.docker.com/desktop/features/wsl/)). Не устанавливайте второй Docker daemon внутри Ubuntu поверх Desktop.

Далее **все команды — в терминале Ubuntu/WSL, не PowerShell**:

```sh
docker version
docker compose version
docker info
python3 --version
git --version
```

Docker должен отвечать серверной частью; Compose нужен >= 2.24.4. В Ubuntu требуются `git` и обычный `python3` для smoke-скрипта. Python 3.14 и Node 24 для самого приложения находятся в образах, отдельно ставить их в WSL не нужно. Если `docker` недоступен, сначала исправьте Desktop/WSL Integration по официальной инструкции; не отключайте защиту Windows и не открывайте Docker socket в сеть.

## 2. Отдельная папка, прежние данные не трогаем

Остановите ранее запущенный native API через Ctrl+C перед копированием/обновлением его файлов. Сохраните его прежнюю папку и фактический путь `BAKEPLAN_DB` без изменений: Docker не использует эту БД автоматически. Не делайте `cp` работающей SQLite и не удаляйте `-wal`/`-shm`.

Используйте **Linux filesystem**, например `~/projects/bakeplan-docker`, а не `/mnt/c/...`. Здесь нужны нормальные Linux permissions, hardlinks и fsync. [Docker рекомендует хранить рабочие файлы в Linux filesystem для WSL](https://docs.docker.com/desktop/features/wsl/best-practices/).

Опубликованную ветку можно получить в отдельный checkout:

```sh
mkdir -p ~/projects
cd ~/projects
git clone --branch feature/bakeplan-ui-polish https://github.com/Nurlan-byte/byte.git bakeplan-docker
cd bakeplan-docker
git rev-parse HEAD
```

Сверьте SHA с переданным результатом проверки. При ошибке clone проверьте адрес и доступность GitHub; не заменяйте ветку старой версией без Docker-файлов. Если `bakeplan-docker` уже существует, не удаляйте её и не сбрасывайте изменения; используйте прежний проверенный checkout либо новое имя каталога. Исходная Windows-папка с данными остаётся запасным вариантом.

## 3. Локальный доступ задаёт владелец

Используем отдельный Compose project `bakeplan-local`. При первом запуске он создаст **новую** базу в именованном Docker volume; при повторных запусках использует тот же volume. Старые native SQLite не перезаписываются.

```sh
PRIVATE_DIR="$HOME/bakeplan-private"
mkdir -p "$PRIVATE_DIR"
chmod 700 "$PRIVATE_DIR"
# -n сохраняет существующий env, если вы уже запускали этот вариант.
cp -n deploy/.env.example "$PRIVATE_DIR/deploy.env"
chmod 600 "$PRIVATE_DIR/deploy.env"
printf 'Путь к файлу доступа: %s/demo_users\n' "$PRIVATE_DIR"
nano "$PRIVATE_DIR/deploy.env"
```

В редакторе установите:

- `BAKEPLAN_AUTH_FILE=` — **полный путь, напечатанный выше**, например `/home/your_linux_name/bakeplan-private/demo_users`. Не буквальный `$HOME` и не Windows-путь.
- `BAKEPLAN_RELEASE=local-defense-v1` для первой сборки; для следующей версии новое имя, чтобы старый образ остался для отката.
- `BAKEPLAN_DB_FILE=bakeplan.sqlite3` для первого запуска. Если значение уже другое, сохраните его: это может быть ранее восстановленная БД.

Сохранить в nano: Ctrl+O, Enter; выйти: Ctrl+X. Hostname/email для HTTPS оставить пустыми: локальный режим их не использует.

Если файла доступа ещё нет, **владелец сам** выбирает уникальный пароль и вводит его в интерактивную команду:

```sh
docker run --rm -it --entrypoint caddy caddy:2.10.2-alpine hash-password
nano "$PRIVATE_DIR/demo_users"
```

В файл впишите одну строку: выбранный логин, пробел, полученный bcrypt-хеш. Без кавычек и без блока `basic_auth`. Пароль не писать в файл, git, чат или аргументы команды. Не брать публичный test-only пароль из CI. Если файл уже есть, используйте существующий; не заменяйте доступ без необходимости.

```sh
chmod 644 "$PRIVATE_DIR/demo_users"
```

Это файл с **хешем**, внутри приватной директории `0700`: режим `0644` нужен контейнеру Caddy без `DAC_OVERRIDE` для чтения bind-mounted файла. Больше никуда файл не копируйте. Без непустого читаемого файла приложение через gateway не запустится.

## 4. Сборка и первый запуск

Из `~/projects/bakeplan-docker`:

```sh
ENV_FILE="$HOME/bakeplan-private/deploy.env"
compose() { docker compose --project-name bakeplan-local --env-file "$ENV_FILE" -f deploy/compose.yaml "$@"; }
compose config --quiet
compose build
compose run --rm --no-deps gateway caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
compose up -d --wait --wait-timeout 90
compose ps
python3 -m scripts.check_demo http://127.0.0.1:8080
```

Smoke-скрипт спросит локальный логин и пароль, проверит auth/API/assets/CSRF и ничего не сохранит. Затем откройте **в браузере Windows** `http://127.0.0.1:8080`, введите тот же логин и пароль.

Если Desktop ещё собирает образы, дождитесь завершения. Если проверка падает, смотрите `compose ps` и `compose logs --tail=100 api gateway`; не стирайте volume и не отключайте auth. Если 8080 занят другим приложением, не завершайте чужой процесс вслепую: согласуйте свободный loopback-порт и обновите URL проверки вместе с mapping.

На защите пройти [DEMO.md](DEMO.md): синтетический import → training ready → ML forecast для даты `2026-10-13`, cutoff `2026-10-11` → ML draft → confirm → CSV export → history. Затем:

```sh
compose restart api
compose up -d --wait --wait-timeout 90
python3 -m scripts.check_demo http://127.0.0.1:8080
```

В браузере проверить, что модель, подтверждённый план, история и CSV сохранились. SQLite лежит в volume `bakeplan-local_sqlite_data`; перезапуск контейнера её не удаляет. Не использовать `docker compose down --volumes`, Docker Desktop factory reset или volume prune. Удаление/сброс Docker Desktop может уничтожить его локальные данные: нужен экспорт backup вне Docker, описанный ниже.

## 5. Резервная копия перед защитой/обновлением

В том же WSL-терминале, с определённой функцией `compose`:

```sh
SNAP="defense-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
compose exec -T api python -m scripts.sqlite_snapshot backup --output "/backups/$SNAP"
compose exec -T api python -m scripts.sqlite_snapshot verify "/backups/$SNAP"
BACKUPS="$HOME/bakeplan-private/backups"
mkdir -p "$BACKUPS"
chmod 700 "$BACKUPS"
compose cp "api:/backups/$SNAP" "$BACKUPS/$SNAP"
compose cp "api:/backups/$SNAP.manifest.json" "$BACKUPS/$SNAP.manifest.json"
chmod 600 "$BACKUPS/$SNAP" "$BACKUPS/$SNAP.manifest.json"
printf 'Сохранена пара backup + manifest: %s/%s\n' "$BACKUPS" "$SNAP"
```

Оба файла нужны вместе. Это копия **вне Docker volume, но на том же компьютере**. Для защиты от потери ноутбука отдельно сохраните пару в разрешённое приватное хранилище/на свой носитель. Не отправляйте её автоматически сторонним сервисам. Не копируйте сам живой файл `bakeplan.sqlite3` вместо SQLite backup API.

## 6. Безопасное восстановление и откат

Этот шаг делать как отдельную репетицию, а не перед самым выступлением. Выберите уже проверенный `SNAP` из `/backups`; скрипт не перезаписывает текущую БД:

```sh
# SNAP должен указывать на существующую проверенную копию, не новое пустое имя.
RESTORED="restored-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
compose stop gateway api
compose run --rm --no-deps api python -m scripts.sqlite_snapshot restore "/backups/$SNAP" --database "/data/$RESTORED" --app-stopped
printf 'Новое имя БД для deploy.env: %s\n' "$RESTORED"
nano "$ENV_FILE"
```

Запишите прежнее `BAKEPLAN_DB_FILE`, затем в env замените **только** его на напечатанное новое имя. Исходная БД сохранена. Продолжите:

```sh
compose up -d --wait --wait-timeout 90
python3 -m scripts.check_demo http://127.0.0.1:8080
```

Сравните прежние datasets, модели, подтверждённые планы, редакции и CSV. Если restore не прошёл, не меняйте env на несуществующий файл. Для отката остановите gateway/API, верните прежнее `BAKEPLAN_DB_FILE` и прежний `BAKEPLAN_RELEASE`, запустите `compose up -d --no-build --wait --wait-timeout 90` и снова проверьте данные. Не удаляйте ни одну БД до принятия результата. Если будущая версия меняет схему, откатывайте согласованную пару image + snapshot, не запускайте старый код на новой схеме.

После обычной работы достаточно `compose stop`; для продолжения — `compose up -d --wait --wait-timeout 90`. В новом WSL-терминале сначала перейдите в checkout и снова определите `ENV_FILE` и функцию `compose` из раздела 4. Перед защитой запустите Docker Desktop заранее, проверьте сценарий без пересборки и сделайте backup. Локальная ссылка работает только на этом компьютере; внешняя ссылка для сдачи требует отдельно выбранного размещения и HTTPS, см. [DEPLOYMENT.md](DEPLOYMENT.md).
