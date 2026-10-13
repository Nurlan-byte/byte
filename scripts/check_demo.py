"""Read-only deployed gateway smoke checks, plus rejected writes to a fake route.

Credentials are prompted locally, never accepted in arguments or printed.
Run only against your authorized demo. This script refuses remote plain HTTP.
"""

import argparse
import base64
import getpass
import ipaddress
import json
import sys
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CheckError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckError("Unexpected redirect; credentials were not forwarded")


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.paths.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet" and attrs.get("href"):
            self.paths.append(attrs["href"])


def validate_base_url(value):
    parts = urlsplit(value)
    if (
        parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise CheckError(
            "Use an origin only, with no credentials, path, query or fragment"
        )
    if not parts.hostname or parts.scheme not in ("https", "http"):
        raise CheckError("Expected an HTTP(S) origin")
    loopback = parts.hostname == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        pass
    if parts.scheme == "http" and not loopback:
        raise CheckError("Remote plain HTTP is forbidden; use valid HTTPS")
    return value.rstrip("/")


def check_demo(base_url, username, password):
    base_url = validate_base_url(base_url)
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    opener = build_opener(NoRedirect())
    results = []

    def request(path, *, auth=False, method="GET", headers=None, expected=200):
        target = urljoin(base_url + "/", path)
        if (
            urlsplit(target).netloc != urlsplit(base_url).netloc
            or urlsplit(target).scheme != urlsplit(base_url).scheme
        ):
            raise CheckError("Frontend refers to a cross-origin asset")
        outgoing = dict(headers or {})
        if auth:
            outgoing["Authorization"] = "Basic " + token
        req = Request(target, method=method, headers=outgoing)
        try:
            response = opener.open(req, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            body = response.read(2_000_000)
            status = response.status
        if status != expected:
            raise CheckError(f"{method} {path}: expected {expected}, got {status}")
        results.append(f"{method} {path}: {status}")
        return body

    for path in (
        "/",
        "/api",
        "/api/health",
        "/api/catalog",
        "/api/datasets",
        "/api/plans",
        "/api/plans/missing/export",
        "/docs",
        "/openapi.json",
        "/assets/missing.js",
    ):
        request(path, expected=401)
    html = request("/", auth=True)
    parser = Assets()
    parser.feed(html.decode("utf-8"))
    if not parser.paths:
        raise CheckError("No built frontend assets found")
    for path in parser.paths:
        request(path, expected=401)
        request(path, auth=True)
    health = json.loads(request("/api/health", auth=True))
    if health.get("status") != "ok" or health.get("db_status") != "ok":
        raise CheckError("Backend health is not ready")
    request("/api/catalog", auth=True)
    # This deliberately nonexistent route cannot mutate application state.
    request("/api/__deployment_probe__", auth=True, method="POST", expected=403)
    request(
        "/api/__deployment_probe__",
        auth=True,
        method="POST",
        headers={"X-BakePlan-Request": "1", "Sec-Fetch-Site": "cross-site"},
        expected=403,
    )
    request(
        "/api/__deployment_probe__",
        method="OPTIONS",
        headers={
            "Origin": "https://untrusted.invalid",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-bakeplan-request",
        },
        expected=401,
    )
    request(
        "/api/__deployment_probe__",
        auth=True,
        method="POST",
        headers={"X-BakePlan-Request": "1"},
        expected=404,
    )
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    args = parser.parse_args(argv)
    try:
        base_url = validate_base_url(args.base_url)
        username = input("Demo username: ")
        password = getpass.getpass("Demo password (not saved): ")
        results = check_demo(base_url, username, password)
    except (CheckError, URLError, OSError, ValueError) as exc:
        print(f"Deployment check failed: {exc}", file=sys.stderr)
        return 1
    print("\n".join(results))
    print(
        "Gateway checks passed. Also run the documented browser/restart/restore drill."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
