"""Requests arriving through a tunnel come from 127.0.0.1 but must be treated as remote (FR5, security)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    from forge.server.app import app

    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


@pytest.mark.parametrize("header", ["cf-connecting-ip", "x-forwarded-for", "cf-ray"])
def test_tunneled_request_loses_builder_access(client, header):
    """A request relayed by cloudflared carries proxy headers and must not get the localhost bypass."""
    created = client.post("/v1/agents", json={"slug": "a1", "name": "A", "system_prompt": "x", "model": "fake:echo"})
    assert created.status_code == 200
    token, key = created.json()["share_token"], created.json()["api_key"]
    remote = {header: "203.0.113.7"}

    assert client.get("/v1/agents", headers=remote).status_code in (401, 403)
    assert client.post("/v1/agents", headers=remote, json={"slug": "a2", "name": "B", "system_prompt": "x"}).status_code in (401, 403)
    assert client.put("/v1/agents/a1/secrets", headers=remote, json={"K": "v"}).status_code == 401
    assert client.get("/v1/agents/a1/versions/prod", headers=remote).status_code == 401
    # Share visitors see only public fields; API key holders keep full access.
    public = client.get(f"/v1/agents/a1?share={token}", headers=remote).json()
    assert set(public) == {"id", "slug", "name", "description", "suggested_prompts"}
    assert client.get("/v1/agents/a1/versions/prod", headers={**remote, "authorization": f"Bearer {key}"}).status_code == 200


def test_direct_localhost_keeps_builder_access(client):
    assert client.get("/v1/agents").status_code == 200
