# Архитектура ежедневного заказа

Модель описывает приватную установку BakePlan. Кассу можно подключить для ежедневного импорта. Погода, внешний мониторинг и передача трасс включаются в настройках. При загрузке CSV администратору достаточно самого приложения.

Схемы получены из [модели C4](workspace.dsl). Нижняя схема прослеживает путь от кассы до рабочего места администратора. React работает в браузере, а планировщик и Chronos-2 работают внутри сервера API. SQLite хранит данные в файле на диске сервера.

## Окружение системы

```mermaid
graph TB
  linkStyle default fill:#ffffff


    1["Администратор филиала<br/>[Person]"]
    style 1 fill:#e8eef8,stroke:#a2a6ad,color:#192c49
    2["SmartShell / Poster / МойСклад<br/>[Software System]"]
    style 2 fill:#ffffff,stroke:#444444,color:#444444
    3["Open-Meteo<br/>[Software System]"]
    style 3 fill:#ffffff,stroke:#444444,color:#444444
    4["Healthchecks.io<br/>[Software System]"]
    style 4 fill:#ffffff,stroke:#444444,color:#444444
    5["Приёмник трасс<br/>[Software System]"]
    style 5 fill:#ffffff,stroke:#444444,color:#444444
    6["BakePlan<br/>[Software System]"]
    style 6 fill:#ffffff,stroke:#444444,color:#444444

    1-. "Проверяет и подтверждает<br />заказ" .->6
    6-. "Запрашивает продажи" .->2
    6-. "Уточняет погоду при настройке" .->3
    6-. "Сообщает о готовности при<br />настройке" .->4
    6-. "Передаёт трассы при настройке" .->5
```

## Контейнеры и потоки данных

```mermaid
graph TB
  linkStyle default fill:#ffffff


    1["Администратор филиала<br/>[Person]"]
    style 1 fill:#e8eef8,stroke:#a2a6ad,color:#192c49
    2["SmartShell / Poster / МойСклад<br/>[Software System]"]
    style 2 fill:#ffffff,stroke:#444444,color:#444444

    subgraph 6 ["BakePlan"]
      style 6 fill:#ffffff,stroke:#444444,color:#444444

      10[("База филиалов<br/>[Container: SQLite]")]
      style 10 fill:#e9f5ee,stroke:#a3aba6,color:#143e2c
      7["Рабочее место<br/>[Container: React, TypeScript]"]
      style 7 fill:#e9f5ee,stroke:#a3aba6,color:#143e2c
      8["Веб-шлюз<br/>[Container: Caddy]"]
      style 8 fill:#e9f5ee,stroke:#a3aba6,color:#143e2c
      9["Сервер BakePlan<br/>[Container: Python, FastAPI]"]
      style 9 fill:#e9f5ee,stroke:#a3aba6,color:#143e2c
    end

    1-. "Работает с заказом" .->7
    7-. "Загружает интерфейс и<br />вызывает API<br/>[HTTP(S)]" .->8
    8-. "Передаёт запросы /api<br/>[HTTP]" .->9
    9-. "Читает и сохраняет данные<br/>[SQL]" .->10
    9-. "Получает продажи филиала<br/>[HTTPS]" .->2
```

## Обновление схем

Скачайте [Structurizr 2026.09.19](https://download.structurizr.com/structurizr-2026.09.19.war). Из корня репозитория запустите команду с установленными Java 21 или новее и Python 3.14. Она проверит контрольную сумму инструмента, разберёт модель и обновит эту страницу.

```shell
python docs/architecture/render.py path/to/structurizr-2026.09.19.war
```

Параметр `--check` сверяет страницу с текущей моделью. [Документация Structurizr](https://docs.structurizr.com/export)
