"""Generate or check the frontend contract from BakePlan's actual POST routes."""
import argparse
import json
from pathlib import Path
import subprocess

from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from backend.app import app

ROOT = Path(__file__).resolve().parents[1]
SUCCESS = {"/api/forecasts": "200", "/api/plans": "201", "/api/plans/{ident}/confirm": "200"}


def order_openapi():
    routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path in SUCCESS and "POST" in r.methods]
    schema = get_openapi(title=app.title, version=app.version, openapi_version=app.openapi_version, routes=routes)
    for path, status in SUCCESS.items():
        response = schema["paths"][path]["post"]["responses"][status]
        if not response["content"]["application/json"]["schema"].get("$ref"):
            raise ValueError(f"POST {path} needs a typed {status} response")
    return schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare with the committed types without rewriting them")
    args = parser.parse_args()
    command = [
        "node", "node_modules/openapi-typescript/bin/cli.js", "--default-non-nullable=false",
        "--output", "src/generated/order-api.d.ts",
    ]
    if args.check:
        command.append("--check")
    return subprocess.run(command, cwd=ROOT / "frontend", input=json.dumps(order_openapi()).encode()).returncode


if __name__ == "__main__":
    raise SystemExit(main())
