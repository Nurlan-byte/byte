"""Immutable, hash-validated local JSON model artifacts and training retries.

Only the application's training workflow writes artifacts. No executable model
serialization is used. SQLite stores the artifact and its idempotency record in
one transaction so a retry after a process restart returns the original model.
"""

import hashlib
import hmac
import json
import math
from datetime import date

from backend import storage


class TrainingConflict(Exception):
    """A training idempotency key or model identity was reused incompatibly."""


class InvalidArtifact(Exception):
    """A model artifact is malformed, non-finite, or fails its integrity check."""


_MODEL_COLUMNS = "id,dataset_id,training_cutoff,protocol_version,payload,artifact_hash"
_SCOPE_FIELDS = ("id", "dataset_id", "training_cutoff", "protocol_version")
_STRING_FIELDS = (
    *_SCOPE_FIELDS,
    "dataset_version",
    "bakery_id",
    "target",
    "unit",
    "status",
)


def _iso_date(value):
    if isinstance(value, date):
        value = value.isoformat()
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("cutoff must be an ISO calendar date")
    return value


def _validate_json(value):
    """Reject values JSON would silently coerce as well as non-finite floats."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidArtifact("Model artifact contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidArtifact("Model artifact keys must be strings")
            _validate_json(item)
        return
    raise InvalidArtifact("Model artifact must contain only JSON values")


def _validate_model(model):
    if not isinstance(model, dict):
        raise InvalidArtifact("Model artifact must be a JSON object")
    for field in _STRING_FIELDS:
        if not isinstance(model.get(field), str) or not model[field].strip():
            raise InvalidArtifact(f"Model artifact requires a nonempty {field}")
    try:
        _iso_date(model["training_cutoff"])
        _validate_json(model)
    except (ValueError, TypeError, RecursionError) as exc:
        raise InvalidArtifact("Model artifact contains invalid JSON or cutoff") from exc
    if not isinstance(model.get("products"), dict) or any(
        not isinstance(product, dict) for product in model["products"].values()
    ):
        raise InvalidArtifact("Model artifact requires product objects")
    if not isinstance(model.get("evaluation"), dict):
        raise InvalidArtifact("Model artifact requires an evaluation object")


def _encode(model):
    _validate_model(model)
    try:
        payload = json.dumps(
            model,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise InvalidArtifact("Model artifact cannot be encoded as JSON") from exc
    return payload, digest


def _reject_constant(value):
    raise InvalidArtifact(f"Invalid JSON number: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidArtifact("Model artifact contains a duplicate JSON key")
        result[key] = value
    return result


def _decode(row):
    if row is None:
        raise InvalidArtifact("Training record references a missing model artifact")
    ident, dataset_id, cutoff, protocol, payload, digest = row
    try:
        if not isinstance(payload, str) or not isinstance(digest, str):
            raise InvalidArtifact("Model artifact or hash has an invalid type")
        actual_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(actual_digest, digest):
            raise InvalidArtifact("Model artifact failed its SHA-256 integrity check")
        model = json.loads(
            payload, parse_constant=_reject_constant, object_pairs_hook=_unique_object
        )
        _validate_model(model)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise InvalidArtifact("Stored model artifact is invalid") from exc
    expected = (ident, dataset_id, cutoff, protocol)
    if tuple(model[field] for field in _SCOPE_FIELDS) != expected:
        raise InvalidArtifact("Model artifact scope does not match its stored identity")
    return model


def _get(con, ident):
    return con.execute(
        f"SELECT {_MODEL_COLUMNS} FROM models WHERE id=?", (ident,)
    ).fetchone()


def get_model(ident):
    with storage.connection() as con:
        row = _get(con, ident)
        if row is None:
            raise storage.NotFound(ident)
        return _decode(row)


def list_models(dataset_id):
    """Return complete validated models, most recent training cutoff first."""
    with storage.connection() as con:
        rows = con.execute(
            f"SELECT {_MODEL_COLUMNS} FROM models WHERE dataset_id=? "
            "ORDER BY training_cutoff DESC,rowid DESC",
            (dataset_id,),
        )
        return [_decode(row) for row in rows]


def find_model(dataset_id, cutoff, protocol_version=None):
    """Find the latest compatible persisted cutoff, or return None if absent."""
    cutoff = _iso_date(cutoff)
    query = (
        f"SELECT {_MODEL_COLUMNS} FROM models WHERE dataset_id=? AND training_cutoff<=?"
    )
    params = [dataset_id, cutoff]
    if protocol_version is not None:
        query += " AND protocol_version=?"
        params.append(protocol_version)
    query += " ORDER BY training_cutoff DESC,rowid DESC LIMIT 1"
    with storage.connection() as con:
        row = con.execute(query, params).fetchone()
        return _decode(row) if row is not None else None


def _previous_training(con, dataset_id, key, body_string):
    previous = con.execute(
        "SELECT body,model_id FROM training_requests WHERE dataset_id=? AND key=?",
        (dataset_id, key),
    ).fetchone()
    if previous is None:
        return None
    if previous[0] != body_string:
        raise TrainingConflict("Training key was already used with different data")
    model = _decode(_get(con, previous[1]))
    if model["dataset_id"] != dataset_id:
        raise InvalidArtifact("Training request references another dataset")
    return model


def previous_training(dataset_id, key, body_string):
    with storage.connection() as con:
        return _previous_training(con, dataset_id, key, body_string)


def save_training(model, key, body_string):
    """Atomically save one immutable scope and its retry key.

    Training for an existing dataset/cutoff/protocol returns that exact artifact,
    even if another request trained concurrently. A new retry key may reference
    it, but neither the model payload nor earlier request records are replaced.
    """
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Training key must be a nonempty string")
    if not isinstance(body_string, str):
        raise ValueError("Training request body must be a string")
    payload, digest = _encode(model)
    # Decode our JSON snapshot so caller mutation cannot change the stored scope
    # or the value returned while this request waits for the write transaction.
    model = json.loads(payload)
    with storage.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        previous = _previous_training(con, model["dataset_id"], key, body_string)
        if previous is not None:
            return previous, False
        row = con.execute(
            f"SELECT {_MODEL_COLUMNS} FROM models WHERE dataset_id=? "
            "AND training_cutoff=? AND protocol_version=?",
            (model["dataset_id"], model["training_cutoff"], model["protocol_version"]),
        ).fetchone()
        created = row is None
        if created:
            if _get(con, model["id"]) is not None:
                raise TrainingConflict(
                    "Model identity already belongs to another scope"
                )
            con.execute(
                f"INSERT INTO models({_MODEL_COLUMNS}) VALUES (?,?,?,?,?,?)",
                (*(model[field] for field in _SCOPE_FIELDS), payload, digest),
            )
        else:
            model = _decode(row)
        con.execute(
            "INSERT INTO training_requests(dataset_id,key,body,model_id) "
            "VALUES (?,?,?,?)",
            (model["dataset_id"], key, body_string, model["id"]),
        )
        return model, created
