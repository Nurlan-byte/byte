"""Optional telemetry for scheduled sales imports. Only opaque IDs and fixed status codes leave the API."""
from collections import Counter
import json
import logging
import os
from contextlib import contextmanager
from functools import lru_cache
from http.client import HTTPException
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from backend import pos_connection_store as store

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _tracer(endpoint):
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from requests import Session

            uri = urlsplit(endpoint)
            if uri.scheme not in {"http", "https"} or not uri.hostname or uri.username or uri.password or uri.query or uri.fragment:
                raise ValueError("Invalid collector endpoint")
            session = Session()
            session.max_redirects = 0
            session.trust_env = False
            provider = TracerProvider(resource=Resource({"service.name": "bakeplan"}))
            exporter = OTLPSpanExporter(endpoint=endpoint, timeout=4, session=session)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            return provider.get_tracer(__name__)
        except (ImportError, OSError, ValueError):
            log.warning("POS trace exporter unavailable")
    return trace.get_tracer(__name__)


@contextmanager
def span(name, **attributes):
    tracer = _tracer(os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", ""))
    # HTTP errors from providers may contain API keys in URLs or raw responses.
    with tracer.start_as_current_span(
        name, attributes=attributes, record_exception=False, set_status_on_exception=False
    ) as current:
        try:
            yield current
        except Exception:
            current.set_status(StatusCode.ERROR)
            raise


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def _send_success(check_id):
    # Accept only a check UUID. Neither credentials nor branch data accompany the signal.
    sender = build_opener(ProxyHandler({}), _NoRedirect())
    req = Request(f"https://hc-ping.com/{UUID(check_id)}", method="HEAD")
    with sender.open(req, timeout=4) as response:
        return response.status == 200


def notify_ready(clock, *, only=None):
    path = os.getenv("BAKEPLAN_HEALTHCHECKS_FILE")
    if not path:
        return
    try:
        mapping = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(mapping, dict):
            raise ValueError("Invalid heartbeat mapping")
    except (OSError, ValueError):
        log.warning("POS heartbeat configuration unavailable")
        return
    entries = {}
    for key, value in mapping.items():
        try:
            entries[str(UUID(key))] = str(UUID(value))
        except (ValueError, TypeError, AttributeError):
            log.warning("Invalid POS heartbeat entry")
    counts = Counter(entries.values())
    for ident, check_id in entries.items():
        if only is not None and ident != only:
            continue
        if counts[check_id] != 1:
            log.warning("POS branches require distinct heartbeat checks")
            continue
        now = clock()
        day = now.astimezone(store.BUSINESS_TZ).date()
        try:
            token = store.claim_ping(ident, day, now)
            if token is None:
                continue
            try:
                success = _send_success(check_id)
            except (OSError, HTTPException, ValueError):
                success = False
            if not success:
                log.warning("POS heartbeat delivery unavailable")
            store.finish_ping(ident, day, token, clock(), success)
        except sqlite3.Error:
            log.warning("POS heartbeat ledger unavailable")
