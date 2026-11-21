"""Daily invoice incidence from D. Chen's Online Retail, UCI (2015), CC BY 4.0.

Source: https://doi.org/10.24432/C5BW33 . Repeated lines for one product on
one positive sales invoice count once. No customer identifiers are exported.
"""
from collections import defaultdict
from datetime import date
import hashlib
from io import BytesIO
import re
from xml.etree import ElementTree
from zipfile import ZipFile

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
SITE = "UCI_Online_Retail"
EXCEL_EPOCH = date(1899, 12, 30).toordinal()


def _lines(archive_path, digest, end):
    with archive_path.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != digest:
            raise ValueError("Unexpected UCI Online Retail archive checksum")
    with ZipFile(archive_path) as archive, ZipFile(BytesIO(archive.read("Online Retail.xlsx"))) as book:
        strings = ["".join(value.itertext()) for value in ElementTree.fromstring(book.read("xl/sharedStrings.xml"))]
        with book.open("xl/worksheets/sheet1.xml") as sheet:
            for _, row in ElementTree.iterparse(sheet, events=("end",)):
                if row.tag != NS + "row":
                    continue
                cells = {}
                if row.get("r") != "1":
                    for cell in row:
                        col = cell.get("r")[0]
                        if col not in ("A", "B", "D", "E", "F"):
                            continue
                        value = cell.findtext(NS + "v")
                        if value is not None:
                            cells[col] = strings[int(value)] if cell.get("t") == "s" else value
                row.clear()
                if not cells:
                    continue
                day = EXCEL_EPOCH + int(float(cells["E"]))
                invoice, sku = cells["A"], cells["B"].upper()
                if (day <= end and re.fullmatch(r"[0-9]{6}", invoice) and re.fullmatch(r"[0-9]{5}[A-Z]?", sku)
                        and float(cells["D"]) > 0 and float(cells["F"]) > 0):
                    yield day, invoice, sku


def load_uci(archive_path, spec):
    """Return ranked stock codes per site and daily counts keyed by (site, SKU).

    Days on which the retailer has no eligible invoices are left missing.
    Both the original ranking and the one knowable at the earliest forecasting
    origin must choose the same products in the same order.
    """
    start, rank_end, first_origin, end = (
        date.fromisoformat(spec[key]).toordinal() for key in ("rank_start", "rank_end", "first_origin", "end")
    )
    daily = defaultdict(lambda: defaultdict(set))
    ranked_invoices = {cutoff: defaultdict(set) for cutoff in (first_origin, rank_end)}
    for day, invoice, sku in _lines(archive_path, spec["archive_sha256"], end):
        daily[day][sku].add(invoice)
        for cutoff, invoices in ranked_invoices.items():
            if start <= day <= cutoff:
                invoices[sku].add(invoice)

    def top(cutoff):
        invoices = ranked_invoices[cutoff]
        return sorted(invoices, key=lambda sku: (-len(invoices[sku]), sku))[:spec["top_n"]]

    products = top(first_origin)
    if len(products) != spec["top_n"] or products != top(rank_end):
        raise ValueError("The preregistered UCI product ranking was not identifiable at the first origin")
    histories = {(SITE, sku): {day: len(items.get(sku, ())) for day, items in sorted(daily.items())}
                 for sku in products}
    return {SITE: products}, histories
