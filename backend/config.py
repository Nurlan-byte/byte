from datetime import datetime, timedelta, timezone

CATALOG = [
    {"product_id": f"p{i}", "name": name, "batch_size": batch, "max_daily_qty": maximum}
    for i, (name, batch, maximum) in enumerate(
        [
            ("Хлеб пшеничный", 12, 100),
            ("Хлеб ржаной", 10, 100),
            ("Багет", 8, 80),
            ("Круассан", 6, 120),
            ("Булочка", 12, 144),
        ],
        1,
    )
]

VERSION = "baseline-2"


def business_today():
    return datetime.now(timezone(timedelta(hours=5))).date()


for _product in CATALOG:
    _product.update(active_from=None, active_to=None, price=None, cost=None)
