import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import fspbx as fspbx_api
from src.core.fspbx_store import FspbxStore
from src.integrations.fspbx import FspbxApiResult


def _client(monkeypatch, tmp_path):
    store = FspbxStore(str(tmp_path / "fspbx.db"))
    monkeypatch.setattr(fspbx_api, "_store", lambda: store)
    monkeypatch.setattr(
        fspbx_api,
        "_active_agent",
        lambda slug: {"slug": slug, "display_name": slug.title(), "is_active": True}
        if slug == "support"
        else None,
    )

    app = FastAPI()
    app.include_router(fspbx_api.router, prefix="/api")
    return TestClient(app), store


def test_connection_and_assignment_crud(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path)

    saved = client.put(
        "/api/fspbx/connection",
        json={
            "base_url": "https://pbx.example.com",
            "token": "secret-token",
            "verify_ssl": True,
            "timeout_ms": 5000,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["connection"]["token_configured"] is True
    assert saved.json()["connection"]["token"] == ""

    def fake_list_domains(self, *, limit=100):
        return FspbxApiResult(
            True,
            200,
            {
                "data": [
                    {
                        "domain_uuid": "11111111-1111-1111-1111-111111111111",
                        "domain_name": "tenant-a.example.com",
                        "domain_description": "Tenant A",
                        "domain_enabled": True,
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "src.integrations.fspbx.FspbxApiClient.list_domains",
        fake_list_domains,
    )

    created = client.post(
        "/api/fspbx/assignments",
        json={
            "domain_uuid": "11111111-1111-1111-1111-111111111111",
            "domain_name": "tenant-a.example.com",
            "domain_description": "Tenant A",
            "agent_slug": "support",
            "enabled": True,
            "notes": "Primary support tenant",
        },
    )
    assert created.status_code == 200
    assignment_id = created.json()["assignment"]["id"]

    domains = client.get("/api/fspbx/domains")
    assert domains.status_code == 200
    assert domains.json()["count"] == 1
    assert domains.json()["domains"][0]["assignment"]["agent_slug"] == "support"

    updated = client.patch(
        f"/api/fspbx/assignments/{assignment_id}",
        json={"notes": "Updated note"},
    )
    assert updated.status_code == 200
    assert updated.json()["assignment"]["notes"] == "Updated note"

    deleted = client.delete(f"/api/fspbx/assignments/{assignment_id}")
    assert deleted.status_code == 200
    assert store.get_assignment(assignment_id) is None


def test_create_assignment_rejects_missing_agent(monkeypatch, tmp_path):
    client, _store = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/fspbx/assignments",
        json={
            "domain_uuid": "22222222-2222-2222-2222-222222222222",
            "agent_slug": "missing-agent",
        },
    )
    assert response.status_code == 422
