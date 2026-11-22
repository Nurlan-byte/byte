"""Look up BakePlan branch IDs for a POS account before assigning a login."""

import argparse
from datetime import date, timedelta
from getpass import GetPassWarning, getpass
import json
import warnings

from backend import pos_import, pos_providers
from backend.config import business_today
from backend.errors import ServiceError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=sorted(pos_import.PROVIDER_NAMES))
    parser.add_argument("--sales-on", type=date.fromisoformat, nargs="?",
                        const=business_today() - timedelta(days=1), metavar="YYYY-MM-DD",
                        help="Сверить товары SmartShell за завершённый день без сохранения ключа или продаж.")
    args = parser.parse_args(argv)
    if args.sales_on is not None and (args.provider != "smartshell" or args.sales_on >= business_today()):
        parser.error("--sales-on принимает завершённый день для SmartShell.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        try:
            key = getpass("POS API key: ")
        except (EOFError, GetPassWarning):
            parser.exit(1, "Откройте терминал с поддержкой скрытого ввода ключа.\n")
    reader = pos_providers.VendorReader(args.provider, key)
    try:
        outlets = pos_providers.locations(reader)
    except ServiceError as exc:
        parser.exit(1, exc.detail + "\n")
    except Exception:
        # Vendor transport failures can include credentials embedded in a URL.
        parser.exit(1, "Не удалось прочитать торговые точки. Проверьте ключ и доступность кассы.\n")
    for outlet in outlets:
        record = {**outlet.public(), "bakery_id": pos_import.branch_id(args.provider, outlet)}
        if args.sales_on:
            try:
                sales = pos_import.read_observations(reader, outlet, args.sales_on, args.sales_on)
            except ServiceError as exc:
                parser.exit(1, exc.detail + "\n")
            record["sales_date"] = str(args.sales_on)
            record["goods"] = [
                {"product_id": product, "name": sales.names[product], "sold": qty}
                for (_, product), qty in sorted(sales.quantities.items())
            ]
        print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
