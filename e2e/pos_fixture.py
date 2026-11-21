"""Serve the real BakePlan API with deterministic Poster responses for browser tests."""

import io
import json
import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import uvicorn

from backend import pos_providers
from backend.app import app


class VendorResponse(io.BytesIO):
    headers = {"Content-Type": "application/json"}


class PosterFixture:
    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        params = parse_qs(url.query)
        key = params["token"][0]
        assert url.hostname == "joinposter.com" and key in {
            "fixture-pieces",
            "fixture-weight",
        }
        if url.path.endswith("spots.getSpots"):
            values = [{"spot_id": 1, "name": "Демо-клуб А"}]
            if key == "fixture-pieces":
                values.append({"spot_id": 2, "name": "Демо-клуб Б"})
        elif url.path.endswith("settings.getAllSettings"):
            values = {"COMPANY_ID": "browser-contract"}
        elif url.path.endswith("menu.getProducts"):
            values = [
                {
                    "product_id": 17,
                    "product_name": "Чай",
                    "weight_flag": "0" if key == "fixture-pieces" else "1",
                    "unit": "p" if key == "fixture-pieces" else "kg",
                }
            ]
            if key == "fixture-pieces":
                values.append(
                    {
                        "product_id": 18,
                        "product_name": "Вода",
                        "weight_flag": "0",
                        "unit": "p",
                    }
                )
        else:
            assert url.path.endswith("transactions.getTransactions")
            start = date.fromisoformat(params["date_from"][0])
            end = date.fromisoformat(params["date_to"][0])
            products = [
                {"product_id": 17, "num": 6 if key == "fixture-pieces" else 0.5}
            ]
            if key == "fixture-pieces":
                products.append({"product_id": 18, "num": 3})
            receipts = [
                {
                    "transaction_id": index + 1,
                    "date_close": f"{start + timedelta(days=index)} 10:00:00",
                    "spot_id": 1,
                    "pay_type": 1,
                    "print_fiscal": 0,
                    "products": products,
                }
                for index in range((end - start).days + 1)
            ]
            values = {
                "count": len(receipts),
                "page": {"page": 1, "count": len(receipts)},
                "data": receipts,
            }
        return VendorResponse(json.dumps({"response": values}).encode())


if __name__ == "__main__":
    pos_providers.build_opener = lambda *args: PosterFixture()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    sock = config.bind_socket()
    Path(os.environ["POS_TEST_PORT_FILE"]).write_text(str(sock.getsockname()[1]))
    uvicorn.Server(config).run(sockets=[sock])
