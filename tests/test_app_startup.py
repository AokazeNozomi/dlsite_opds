"""Smoke test that the FastAPI app starts via its lifespan hook."""

from fastapi.testclient import TestClient


def test_app_starts_and_healthz_ok(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DLSITE_OPDS_DATA_DIR", str(tmp_path))
    from dlsite_opds.app import app

    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.text == "ok"
