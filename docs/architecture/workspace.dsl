workspace "BakePlan" "Ежедневный заказ филиала в приватной установке BakePlan." {
    !impliedRelationships false

    model {
        operator = person "Администратор филиала" "Проверяет продажи и остатки, подтверждает заказ и скачивает CSV."
        pos = softwareSystem "Poster / МойСклад" "Касса филиала предоставляет наблюдения о продажах."
        weather = softwareSystem "Open-Meteo" "Возвращает данные о погоде для выбранного города."
        health = softwareSystem "Healthchecks.io" "Принимает сигнал о готовности свежего расчёта, если настроен."
        collector = softwareSystem "Приёмник трасс" "Получает технические трассы по адресу, заданному оператором."

        bakeplan = softwareSystem "BakePlan" "Готовит прогноз и заказ, хранит подтверждённые количества и поставки." {
            ui = container "Рабочее место" "Приложение в браузере администратора." "React, TypeScript"
            gateway = container "Веб-шлюз" "Проверяет доступ, отдаёт интерфейс и направляет запросы API." "Caddy"
            api = container "Сервер BakePlan" "Обрабатывает HTTP и запускает ежедневную синхронизацию. Оба пути используют общий расчёт прогноза. Chronos-2 работает здесь же." "Python, FastAPI"
            store = container "База филиалов" "Хранит снимки продаж, зашифрованные кассовые ключи, заказы и журнал готовности." "SQLite" "Database"
        }

        operator -> bakeplan "Проверяет и подтверждает заказ"
        bakeplan -> pos "Запрашивает продажи"
        bakeplan -> weather "Уточняет погоду при настройке"
        bakeplan -> health "Сообщает о готовности при настройке"
        bakeplan -> collector "Передаёт трассы при настройке"

        operator -> ui "Работает с заказом"
        ui -> gateway "Загружает интерфейс и вызывает API" "HTTP(S)"
        gateway -> api "Передаёт запросы /api" "HTTP"
        api -> store "Читает и сохраняет данные" "SQL"
        api -> pos "Получает продажи филиала" "HTTPS"
        api -> weather "Запрашивает погоду для города" "HTTPS"
        api -> health "Подтверждает готовность расчёта" "HTTPS"
        api -> collector "Отправляет трассы обновления" "OTLP/HTTP"
    }

    views {
        systemContext bakeplan Context {
            title "BakePlan и его окружение"
            include *
            autoLayout tb
        }
        container bakeplan Containers {
            title "Как устроен BakePlan"
            include operator ui gateway api store pos
            autoLayout tb
        }
        styles {
            element "Person" {
                background #e8eef8
                color #192c49
            }
            element "Container" {
                background #e9f5ee
                color #143e2c
            }
            element "Database" {
                shape Cylinder
            }
        }
        properties {
            "mermaid.title" "false"
        }
    }
}
