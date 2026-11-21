# Проверка прогноза BakePlan на открытых данных

Сравнивали прогноз BakePlan с B1 — средним по четырём последним доступным таким же дням недели за восемь недель. Результаты относятся к указанным товарам и периодам. Ошибка — среднее абсолютное отклонение прогноза от факта на один товар в одной точке за день. Меньше — лучше.

| Выборка | Проверочный период | Пар прогнозов | Единица | B1 | BakePlan | Изменение ошибки |
|---|---|---:|---|---:|---:|---:|
| [Французская пекарня, 6 изделий](results/bakery/metrics.csv) | 02.07–30.09.2022 | 534 | изделие, с учётом возвратов | 36,365 | 24,826 | −31,73% |
| [Индонезия, 3 точки общепита](results/cafe/metrics.csv) | 06.08–30.09.2025 | 1 008 | чек с конкретным вариантом напитка | 0,767 | 0,713 | −7,03% |
| [США, вендинговый аппарат](results/vending/metrics.csv) | 02.10–31.12.2022 | 420 | проданная единица товара | 0,677 | 0,687 | +1,52% |
| [Магазин №367, панель покупателей](results/grocery/metrics.csv) | 06.11–31.12.2017 | 440 | единица, купленная участником панели | 0,365 | 0,367 | +0,45% |

В пекарне улучшение относительно B1 наблюдалось по каждому из шести изделий и в 12 из 13 недель. Традиционный багет дал 69% суммарного снижения ошибки. Для остальных пяти изделий вместе она снизилась на 21,97%. На этих данных удалось проверить прогноз зарегистрированных продаж в закупочном режиме. Производственному режиму нужны также сведения об остатках и наличии товара.

Для пекарни сильным ориентиром оказались продажи ровно семь дней назад (B0). На 522 случаях, где доступны все три прогноза, средняя ошибка составила 26,14 у B0, 36,01 у B1 и 25,05 у BakePlan. Преимущество BakePlan перед B0 составило 4,16%. В кафе ошибка снизилась во всех трёх точках относительно обоих ориентиров. [Сравнение на общих датах](results/secondary_b0_metrics.csv) · [Результаты по неделям](results/weekly.csv)

В вендинге и панели продажи выбранных позиций были редкими. В 291 из 420 сочетаний «товар и день» в автомате и в 387 из 440 в панели покупок выбранного товара не зарегистрировано. В таких условиях базовый метод дал меньшую ошибку на обеих выборках.

## Что именно проверяли

Использовали [версию прогнозного ядра ac3c2af](https://github.com/Nurlan-byte/byte/blob/ac3c2af5a4535c6fcd35ccd7ebdd41c4f7bac4cb/backend/ml_core.py). Товары и точки отбирали только по предшествующей истории. Для каждой целевой даты брали факты максимум по дату двумя днями ранее. В пекарне, кафе и вендинге модель обновляли еженедельно, для панели покупателей зафиксировали модель от 4 ноября 2017 года. В таблицы включены как прогнозы Ridge, так и случаи, когда BakePlan выбрал B1.

По файлам можно проследить дату, доступный срез истории, дату обучения, товар, метод и оба прогноза. [Пекарня](results/bakery/forecasts.csv) · [Кафе](results/cafe/forecasts.csv) · [Вендинг](results/vending/forecasts.csv) · [Панель покупателей](results/grocery/forecasts.csv)

В таблицах `metrics.csv` показатель `error_reduction_pct` положителен при снижении ошибки. WAPE — суммарная абсолютная ошибка, делённая на сумму фактических значений, в процентах. При отсутствии фактических продаж знаменатель равен нулю, поле остаётся пустым. Дни без какой-либо зарегистрированной операции в точке пропущены. На наблюдаемый день отсутствие покупки выбранной позиции означает ноль зарегистрированных покупок. Спрос покупателей, которым товар не достался, неизвестен.

## Данные

| Источник | Отбор и величина прогноза |
|---|---|
| [French bakery daily sales, Matthieu Gimbert, v1](https://www.kaggle.com/datasets/matthieugimbert/french-bakery-daily-sales) | Шесть самых продаваемых изделий по чистому количеству до 30.06.2022. Учтены возвраты, из отбора исключена услуга нарезки COUPE. |
| [Anonymous Transactional Dataset, Engel, Gaol, Kurniawan и Budiharto, v2, CC BY 4.0](https://doi.org/10.17632/kcgf45y24m.2) | Из 53 820 уникальных чеков рассчитано число чеков с конкретным напитком. По шесть вариантов кофе или чая в каждой из трёх точек, отбор до 04.08.2025 включительно. Количество порций отдельного напитка в чеке не указано. [Лицензия](https://creativecommons.org/licenses/by/4.0/) |
| [Vending Machine Sales, Aniruddh Singh, v5, CC0](https://www.kaggle.com/datasets/awesomeasingh/vending-machine-sales) | Из пяти аппаратов выбран GuttenPlans x1367 по продажам до 30.09.2022. Шесть лидеров среди товаров с продажами в каждом из трёх первых кварталов и в сентябре. |
| [The Complete Journey, 84.51°; пакет Bradley Boehmke](https://bradleyboehmke.github.io/completejourney/) | Панель 2 469 домохозяйств. Магазин выбран по числу товарных строк в подходящих категориях до 04.11.2017. Восемь позиций отобраны по частоте дней с покупками и объёму. [Закреплённые файлы](https://github.com/bradleyboehmke/completejourney/tree/5b5d06192b9856edd04e4d405787af2f2e4a1fef/data) |

## Повторный запуск

Команды рассчитаны на запуск из корня репозитория в окружении Python 3.14. Скрипты берутся из текущего дерева, а историческое ядро — из отдельной копии на коммите `ac3c2af`. Обучение и прогноз требуют NumPy, для чтения данных панели также нужны pandas, pyreadr и база часовых поясов. Полные парные результаты с фактическими наблюдениями сохраняются в локальной папке `private`.

```sh
git worktree add --detach ../bakeplan-open-sales-core ac3c2af5a4535c6fcd35ccd7ebdd41c4f7bac4cb
python -m pip install numpy==2.5.3 pandas==3.1.1 pyreadr==0.5.9 tzdata
```

Поместите исходные архивы Kaggle в `private/bakery.zip` и `private/vending.zip`, а файлы Mendeley `Transactions.csv` и `Products.csv` в `private/cafe-source/`. Скрипт панели загружает закреплённые таблицы в указанную папку при первом запуске. Контрольные суммы входных файлов проверяются при чтении. [ZIP пекарни, v1](https://api.kaggle.com/v1/datasets/download/matthieugimbert/french-bakery-daily-sales?datasetVersionNumber=1) · [ZIP вендинга, v5](https://api.kaggle.com/v1/datasets/download/awesomeasingh/vending-machine-sales?datasetVersionNumber=5) · [Файлы кафе, v2](https://data.mendeley.com/datasets/kcgf45y24m/2)

```sh
python benchmarks/open-sales/scripts/evaluate_bakery.py --repo ../bakeplan-open-sales-core --input private/bakery.zip --out private/bench/bakery
python benchmarks/open-sales/scripts/evaluate_cafe.py --repo ../bakeplan-open-sales-core --data private/cafe-source --out private/bench/cafe
python benchmarks/open-sales/scripts/evaluate_vending.py --repo ../bakeplan-open-sales-core --input private/vending.zip --out private/bench/vending
python benchmarks/open-sales/scripts/evaluate_grocery.py --repo ../bakeplan-open-sales-core --data private/grocery-source --out private/bench/grocery
python benchmarks/open-sales/scripts/export_results.py --bakery private/bench/bakery --cafe private/bench/cafe --vending private/bench/vending --grocery private/bench/grocery --out private/comparison
python benchmarks/open-sales/scripts/weekly.py --bakery-forecasts private/bench/bakery/bakery_forecasts.csv --cafe-forecasts private/bench/cafe/cafe_predictions.csv --out private/comparison/weekly.csv
python benchmarks/open-sales/scripts/check_alternatives.py --repo ../bakeplan-open-sales-core --french-source private/bakery.zip --french-forecasts private/bench/bakery/bakery_forecasts.csv --cafe-source private/cafe-source/Transactions.csv --cafe-forecasts private/bench/cafe/cafe_predictions.csv --out private/comparison/secondary_b0_metrics.csv
```

## Проверка после изменения

Когда готовы четыре локальных файла с прогнозами и фактами и отчёт панели `completejourney_results.json`, повторную сверку можно выполнить стандартным Python. Команда читает эти файлы и опубликованные `results/<выборка>/forecasts.csv` и `metrics.csv`. Она проверяет уникальность пар «точка, товар, дата», проверочный период, горизонт в два дня и то, что записанная дата обучения не позже среза прогноза. Для каждой пары сверяются оба прогноза и выбранный метод, для каждого опубликованного среза заново считаются число пар, частота ML, MAE, WAPE и изменение ошибки.

```sh
python benchmarks/open-sales/scripts/verify_results.py --bakery private/bench/bakery --cafe private/bench/cafe --vending private/bench/vending --grocery private/bench/grocery
```

Успех означает, что показатели исторического ядра `ac3c2af` воспроизводятся по сохранённым исходам. Команда выводит JSON, включая отрицательное снижение ошибки там, где лучше B1, и завершает работу с ошибкой при расхождении. Для оценки изменённой модели нужен отдельный прогон новой версии на заранее выбранном проверочном периоде.
