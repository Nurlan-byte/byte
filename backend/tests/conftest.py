import pytest


@pytest.fixture(autouse=True)
def isolated_local_demo(monkeypatch):
    """Original single-operator scenarios use their own temporary local databases."""
    monkeypatch.setenv("BAKEPLAN_LOCAL_DEMO", "1")
