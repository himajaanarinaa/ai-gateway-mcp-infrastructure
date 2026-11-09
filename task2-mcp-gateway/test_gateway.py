import json

from fastapi.testclient import TestClient
import pytest

import gateway
import mock_server


class FakeResponse:
    status_code = 200

    def __init__(self, request_id):
        self.content = json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"ok": True},
        }).encode()

    def raise_for_status(self):
        return None


class FakeAsyncClient:
    calls = []
    post_error = None

    def __init__(self, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, *, content, headers):
        self.calls.append((url, content, headers))
        if self.post_error is not None:
            raise self.post_error
        return FakeResponse(json.loads(content)["id"])


@pytest.fixture(autouse=True)
def fake_http_client(monkeypatch):
    FakeAsyncClient.calls = []
    FakeAsyncClient.post_error = None
    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeAsyncClient)


def test_viewer_can_list_tools_and_payload_is_forwarded_unchanged():
    client = TestClient(gateway.app)
    body = b'{"jsonrpc":"2.0","id":7,"method":"tools/list","params":{}}'

    response = client.post(
        "/",
        content=body,
        headers={"Authorization": "Bearer viewer-token", "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == 7
    assert len(FakeAsyncClient.calls) == 1
    assert FakeAsyncClient.calls[0][1] == body
    assert FakeAsyncClient.calls[0][2] == {"Content-Type": "application/json"}


def test_admin_can_call_admin_tool():
    client = TestClient(gateway.app)
    response = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "admin_reset_key"},
        },
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == 8
    assert len(FakeAsyncClient.calls) == 1


def test_viewer_admin_call_is_blocked_without_forwarding():
    client = TestClient(gateway.app)
    response = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": "blocked-1",
            "method": "tools/call",
            "params": {"name": "admin_reset_key"},
        },
        headers={"Authorization": "Bearer viewer-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "blocked-1",
        "error": {"code": -32001, "message": "Unauthorized Tool Call"},
    }
    assert FakeAsyncClient.calls == []


def test_missing_and_invalid_tokens_are_rejected():
    client = TestClient(gateway.app)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

    missing = client.post("/", json=body)
    invalid = client.post("/", json=body, headers={"Authorization": "Bearer wrong-token"})

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert FakeAsyncClient.calls == []


def test_invalid_json_and_missing_params_return_json_rpc_errors():
    client = TestClient(gateway.app)
    headers = {"Authorization": "Bearer viewer-token", "Content-Type": "application/json"}

    invalid_json = client.post("/", content=b"{", headers=headers)
    missing_params = client.post(
        "/",
        json={"jsonrpc": "2.0", "id": 42, "method": "tools/call"},
        headers={"Authorization": "Bearer viewer-token"},
    )

    assert invalid_json.status_code == 400
    assert invalid_json.json()["error"]["code"] == -32700
    assert missing_params.json() == {
        "jsonrpc": "2.0",
        "id": 42,
        "error": {"code": -32602, "message": "Invalid params"},
    }
    assert FakeAsyncClient.calls == []


def test_mock_server_supports_tool_listing_and_calls():
    client = TestClient(mock_server.app)

    listed = client.post(
        "/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    called = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_customer_record"},
        },
    )

    assert [tool["name"] for tool in listed.json()["result"]["tools"]] == [
        "get_customer_record",
        "admin_reset_key",
    ]
    assert called.json()["id"] == 2
    assert "completed" in called.json()["result"]["content"][0]["text"]


def test_downstream_timeout_does_not_expose_internal_details():
    import httpx

    FakeAsyncClient.post_error = httpx.ReadTimeout("private connection detail")
    response = TestClient(gateway.app).post(
        "/",
        json={"jsonrpc": "2.0", "id": 13, "method": "tools/list", "params": {}},
        headers={"Authorization": "Bearer viewer-token"},
    )

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 13,
        "error": {"code": -32603, "message": "Downstream request timed out"},
    }


def test_downstream_http_failure_does_not_expose_internal_details():
    import httpx

    FakeAsyncClient.post_error = httpx.ConnectError("private connection detail")
    response = TestClient(gateway.app).post(
        "/",
        json={"jsonrpc": "2.0", "id": 14, "method": "tools/list", "params": {}},
        headers={"Authorization": "Bearer viewer-token"},
    )

    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 14,
        "error": {"code": -32603, "message": "Downstream request failed"},
    }
