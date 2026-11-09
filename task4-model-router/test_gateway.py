import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

import gateway
from rate_limiter import SQLiteRateLimiter


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self.data = data or {"completion": "primary answer"}

    def json(self):
        return self.data


class FakeAsyncClient:
    responses = []
    calls = []

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json):
        type(self).calls.append((url, json, self.timeout))
        value = type(self).responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if asyncio.iscoroutine(value):
            value = await value
        return value


@pytest.fixture(autouse=True)
def gateway_setup(monkeypatch, tmp_path):
    FakeAsyncClient.calls = []
    FakeAsyncClient.responses = []
    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeAsyncClient)
    limiter = SQLiteRateLimiter(str(tmp_path / "usage.sqlite3"))
    monkeypatch.setattr(gateway, "get_limiter", lambda: limiter)
    return limiter


def completion_request(client, api_key="tenant-a", prompt="Hello"):
    return client.post(
        "/complete",
        json={"prompt": prompt},
        headers={"X-API-Key": api_key},
    )


def test_successful_primary_response():
    FakeAsyncClient.responses = [FakeResponse(data={"model": "primary", "completion": "ok"})]

    response = completion_request(TestClient(gateway.app))

    assert response.status_code == 200
    assert response.json()["model"] == "primary"
    assert len(FakeAsyncClient.calls) == 1
    assert FakeAsyncClient.calls[0][0] == gateway.PRIMARY_URL


def test_primary_429_falls_back_once():
    FakeAsyncClient.responses = [
        FakeResponse(status_code=429),
        FakeResponse(data={"model": "backup", "completion": "ok"}),
    ]

    response = completion_request(TestClient(gateway.app))

    assert response.status_code == 200
    assert response.json()["model"] == "backup"
    assert [call[0] for call in FakeAsyncClient.calls] == [
        gateway.PRIMARY_URL,
        gateway.BACKUP_URL,
    ]


def test_primary_timeout_falls_back_once():
    FakeAsyncClient.responses = [
        httpx.ReadTimeout("private timeout"),
        FakeResponse(data={"model": "backup", "completion": "ok"}),
    ]

    response = completion_request(TestClient(gateway.app))

    assert response.status_code == 200
    assert response.json()["model"] == "backup"
    assert FakeAsyncClient.calls[0][2] == 3.0
    assert len(FakeAsyncClient.calls) == 2
    assert FakeAsyncClient.calls[1][0] == gateway.BACKUP_URL


def test_slow_primary_times_out_and_falls_back(monkeypatch):
    async def slow_response():
        await asyncio.sleep(0.05)
        return FakeResponse()

    monkeypatch.setattr(gateway, "PRIMARY_TIMEOUT", 0.01)
    FakeAsyncClient.responses = [
        slow_response(),
        FakeResponse(data={"model": "backup", "completion": "ok"}),
    ]
    response = completion_request(TestClient(gateway.app))

    assert response.status_code == 200
    assert response.json()["model"] == "backup"
    assert [call[0] for call in FakeAsyncClient.calls] == [
        gateway.PRIMARY_URL,
        gateway.BACKUP_URL,
    ]


def test_primary_non_429_failure_does_not_fallback():
    FakeAsyncClient.responses = [FakeResponse(status_code=500)]

    response = completion_request(TestClient(gateway.app))

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "PROVIDER_UNAVAILABLE",
            "message": "Unable to complete the request",
        }
    }
    assert len(FakeAsyncClient.calls) == 1


def test_both_providers_failing_returns_generic_error():
    FakeAsyncClient.responses = [
        FakeResponse(status_code=429),
        httpx.ConnectError("private upstream details"),
    ]

    response = completion_request(TestClient(gateway.app))

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "PROVIDER_UNAVAILABLE",
            "message": "Unable to complete the request",
        }
    }
    assert "private upstream details" not in response.text


def test_token_limit_and_separate_tenants():
    client = TestClient(gateway.app)
    FakeAsyncClient.responses = [FakeResponse(), FakeResponse()]
    at_limit_prompt = "x" * 200_000

    first = completion_request(client, "tenant-a", at_limit_prompt)
    separate = completion_request(client, "tenant-b", at_limit_prompt)
    blocked = completion_request(client, "tenant-a", "x")

    assert first.status_code == 200
    assert separate.status_code == 200
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert len(FakeAsyncClient.calls) == 2


def test_missing_api_key_and_invalid_request_are_standardized():
    client = TestClient(gateway.app)
    missing_key = client.post("/complete", json={"prompt": "Hi"})
    invalid_body = client.post(
        "/complete",
        json={"prompt": ""},
        headers={"X-API-Key": "tenant-a"},
    )

    assert missing_key.status_code == 401
    assert missing_key.json()["error"]["code"] == "INVALID_API_KEY"
    assert invalid_body.status_code == 422
    assert invalid_body.json()["error"]["code"] == "INVALID_REQUEST"


def test_sliding_window_removes_expired_records_and_persists_on_disk(tmp_path):
    database = tmp_path / "persistent.sqlite3"
    limiter = SQLiteRateLimiter(str(database), limit=100, window_seconds=60)

    assert limiter.consume("tenant", 70, now=1000) == (True, 70)
    assert limiter.total_used("tenant", now=1001) == 70
    assert limiter.consume("tenant", 40, now=1061) == (True, 40)
    restarted = SQLiteRateLimiter(str(database), limit=100, window_seconds=60)

    with sqlite3.connect(str(database)) as connection:
        count = connection.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0]
    assert count == 1
    assert database.is_file()
    assert limiter.total_used("tenant", now=1061) == 40
    assert restarted.total_used("tenant", now=1061) == 40


def test_concurrent_requests_do_not_exceed_tenant_limit(tmp_path):
    limiter = SQLiteRateLimiter(str(tmp_path / "concurrent.sqlite3"), limit=50_000)

    def consume_one():
        return limiter.consume("same-tenant", 1_000)[0]

    with ThreadPoolExecutor(max_workers=20) as executor:
        accepted = list(executor.map(lambda _: consume_one(), range(60)))

    assert sum(accepted) == 50
    assert limiter.total_used("same-tenant") == 50_000
