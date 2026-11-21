import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.schemas import ErrorResponse
from tools.order_api import order_openapi


def test_types_are_generated_from_the_published_operations():
    contract = order_openapi()
    public = app.openapi()
    assert "/api/reports/issued-forecasts" in contract["paths"]
    for path, methods in contract["paths"].items():
        method = "get" if path == "/api/reports/issued-forecasts" else "post"
        assert methods == {method: public["paths"][path][method]}
    for name, schema in contract["components"]["schemas"].items():
        assert schema == public["components"]["schemas"][name]


@pytest.mark.parametrize(("method", "path"), [
    ("post", "/api/forecasts"), ("post", "/api/plans"), ("post", "/api/plans/{ident}/confirm"),
    ("get", "/api/reports/issued-forecasts"),
])
def test_invalid_requests_use_the_documented_error(method, path, tmp_path, monkeypatch):
    monkeypatch.setenv("BAKEPLAN_DB", str(tmp_path / "errors.sqlite3"))
    with TestClient(app) as client:
        response = client.request(method, path.replace("{ident}", "invalid"))
    assert response.status_code == 422
    envelope = ErrorResponse.model_validate(response.json())
    assert envelope.model_dump() == response.json()
    assert envelope.error.code == "INVALID_REQUEST" and envelope.error.details
    documented = app.openapi()["paths"][path][method]["responses"]["422"]
    assert documented["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
