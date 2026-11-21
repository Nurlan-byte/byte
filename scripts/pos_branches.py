"""Look up BakePlan branch IDs for a Poster or MoySklad account before assigning a login."""

import argparse
from getpass import GetPassWarning, getpass
import json
import warnings

from backend import pos_import, pos_providers


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=sorted(pos_import.PROVIDER_NAMES))
    args = parser.parse_args(argv)
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        try:
            key = getpass("POS API key: ")
        except (EOFError, GetPassWarning):
            parser.exit(1, "Откройте терминал с поддержкой скрытого ввода ключа.\n")
    reader = pos_providers.VendorReader(args.provider, key)
    try:
        outlets = pos_providers.locations(reader)
    except Exception:
        # Vendor transport failures can include credentials embedded in a URL.
        parser.exit(1, "Не удалось прочитать торговые точки. Проверьте ключ и доступность кассы.\n")
    for outlet in outlets:
        record = {**outlet.public(), "bakery_id": pos_import.branch_id(args.provider, outlet)}
        print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
