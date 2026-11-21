"""Branch authority for a single request. Background jobs run outside this scope."""

from contextlib import contextmanager
from contextvars import ContextVar

from backend.errors import ServiceError


_branch = ContextVar("bakeplan_branch", default=None)


def current_branch() -> str | None:
    return _branch.get()


@contextmanager
def branch_scope(bakery_id):
    token = _branch.set(bakery_id)
    try:
        yield
    finally:
        _branch.reset(token)


def require_branch(bakery_id):
    if current_branch() is not None and bakery_id != current_branch():
        raise ServiceError(404, "Объект не найден")
