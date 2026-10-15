# Осторожная очистка, 15.10.2026

Исходный SHA: `13e3c2f433c0855c39a349c42c80dfd0bc249bec`, default branch `feature/bakeplan-scaffold`; отдельный clone и ветка `chore/repository-cleanup-20261015`. Исходные локальные копии проверены: tracked/untracked изменений нет; игнорируемые данные и артефакты оставлены на месте. UI-ветка `feature/bakeplan-owner-ux` на `bbf516958924cd8a673329ca7ef2dfbdfe0b2311` не включена.

## Что перенесено и исправлено

- Старые verification/MVP audit отчёты перенесены в [архив](ARCHIVE.md) с исторической маркировкой и исправленными относительными ссылками. Уникальные сведения и авторство сохранены.
- CSV/API/SQLite и описание структуры вынесены из основного README в [DATA_CONTRACT](DATA_CONTRACT.md); README остаётся входом в продукт и quickstart.
- [Два отчёта выбора кандидатов](../tools/protocol_v3/README.md#saved-evidence-and-local-output) перенесены побайтно в `tools/protocol_v3/archive/`. Проверены ссылки, CI, Docker, scripts, tests и динамическое чтение/запись: `candidates.py` создаёт исходные имена как outputs; runtime и тесты не читают эти JSON. Скрипт и методика не менялись.
- Quickstart клонирует текущую default branch. Актуальные проверки отделены от истории, открытая работа — от старых сроков. Исправлены отсутствующий `tools/requirements.txt` в инструкции и v2/v3 числа в описании снимков; сами расчёты сохранены.
- `.gitignore` исключает воспроизводимые локальные отчёты, coverage/npm logs и локальные operator env/backups. Текущий tracked audit явно сохранён; архивные JSON остаются tracked. Ignore не удаляет существующие файлы.

## Что оставлено

Tracked кэшей/build output/логов и старых agent tasks не найдено. Исходный код, CSS и зависимости не удалялись: отсутствие импорта или npm-script не доказывает ненужность. В частности, Prettier может использоваться вручную редактором; frozen v2 нужен для исследования и integrity tests.

`frontend/public/demo.csv` совпадает с fixture, но нужен HTTP-загрузке демо; fixture нужен тестам. `confirm_audit.json` — источник таблиц EVALUATION; протоколы, provenance и sales-source attribution сохранены. Сохранены все lock-файлы, миграции, fixtures, тесты, deploy-конфигурация и материалы защиты/записи. Пользовательские SQLite, backups, настройки, секреты и любые файлы вне отдельного clone не очищались.

## Проверки и обратимость

Фактические прогоны и CI — в [VERIFICATION](../VERIFICATION.md). Новые коммиты обычные, история/существующие авторы не переписываются. Для отмены применить `git revert <cleanup-commit>` к актуальной ветке и повторить проверки; не сбрасывать default branch на исходный SHA. Публикация отдельной UI-ветки и изменение работающей VM не входят в эту задачу.
