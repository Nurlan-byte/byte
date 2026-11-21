"""Small process-local cache of completed automatic forecasts. No sales histories are retained."""
from collections import OrderedDict
import hashlib
import json
from threading import Lock

MAX_ENTRIES = 32
MAX_RESULT_BYTES = 128 * 1024
MAX_INPUT_ROWS = 10_000
MAX_CONTEXT_ROWS = 2_000
_results = OrderedDict()
_lock = Lock()


def forecast_key(dataset, target, cutoff, products, protocol):
    # The digest includes the actual in-memory sales and weather sent to the
    # engine, including corrections to a snapshot whose metadata was reused.
    if (not dataset.get("id") or not dataset.get("version") or
            len(products) > 50 or len(dataset["rows"]) > MAX_INPUT_ROWS):
        return None
    context = dataset.get("demand_context")
    if context is not None and (not isinstance(context, dict) or
            sum(len(context.get(field, ())) for field in ("calendar", "weather")) > MAX_CONTEXT_ROWS):
        return None
    inputs = (
        protocol, dataset["id"], dataset["version"], dataset.get("owner_bakery_id"),
        dataset.get("bakery_id"), dataset.get("target"), dataset.get("unit"),
        target.isoformat(), cutoff.isoformat(), products, dataset["rows"], context,
    )
    try:
        return hashlib.sha256(json.dumps(inputs, separators=(",", ":")).encode()).digest()
    except TypeError:
        # Programmatic callers can supply validated date objects in the context.
        # For these in-memory inputs the engine performs its usual validation.
        return None


def lookup(key):
    if key is None:
        return None
    with _lock:
        value = _results.get(key)
        if value is not None:
            _results.move_to_end(key)
    return json.loads(value) if value is not None else None


def remember(key, predictions):
    if key is None:
        return
    value = json.dumps(predictions, separators=(",", ":"), allow_nan=False).encode()
    if len(value) > MAX_RESULT_BYTES:
        return
    with _lock:
        _results[key] = value
        _results.move_to_end(key)
        if len(_results) > MAX_ENTRIES:
            _results.popitem(last=False)
