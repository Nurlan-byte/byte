"""Generate the frontend contract for order workflows from the published OpenAPI schema."""
import argparse
import json
from pathlib import Path
import subprocess

from backend.app import app

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = {
    "/api/forecasts": ("post", "200"),
    "/api/plans": ("post", "201"),
    "/api/plans/{ident}/confirm": ("post", "200"),
    "/api/reports/issued-forecasts": ("get", "200"),
}


def order_openapi():
    public = app.openapi()
    paths = {}
    for path, (method, status) in OPERATIONS.items():
        operation = public["paths"][path][method]
        response = operation["responses"][status]
        if not response["content"]["application/json"]["schema"].get("$ref"):
            raise ValueError(f"{method.upper()} {path} needs a typed {status} response")
        paths[path] = {method: operation}
    schemas = {}
    pending = [paths]
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, dict):
            pending.extend(node.values())
            if "$ref" in node:
                name = node["$ref"].removeprefix("#/components/schemas/")
                if name not in schemas:
                    schemas[name] = public["components"]["schemas"][name]
                    pending.append(schemas[name])
    components = {"schemas": {name: schemas[name] for name in sorted(schemas)}}
    return {"openapi": public["openapi"], "info": public["info"], "paths": paths, "components": components}


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
