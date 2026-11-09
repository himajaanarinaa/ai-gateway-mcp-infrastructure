import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import gateway
import mock_provider
from guardrails import IncrementalRedactor, redact


def sse(text):
    return f"data: {json.dumps({'text': text})}\n\n".encode()


class FakeResponse:
    status_code = 200

    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class FakeClient:
    response = None
    send_error = None
    closed = False

    def __init__(self, **kwargs):
        self.timeout = kwargs.get("timeout")
        type(self).closed = False

    def build_request(self, method, url, json):
        self.request = (method, url, json)
        return self.request

    async def send(self, request, stream=False):
        if type(self).send_error:
            raise type(self).send_error
        return type(self).response

    async def aclose(self):
        type(self).closed = True


@pytest.fixture(autouse=True)
def fake_provider_client(monkeypatch):
    FakeClient.response = FakeResponse([])
    FakeClient.send_error = None
    FakeClient.closed = False
    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeClient)


def test_redacts_email_ssn_and_credit_card():
    text = "Email jane.doe@example.com SSN 123-45-6789 card 4111 1111 1111 1111"
    result = redact(text)

    assert result == "Email [REDACTED] SSN [REDACTED] card [REDACTED]"


@pytest.mark.parametrize(
    "parts",
    [
        ["Reach alex@", "example.com now"],
        ["SSN 123-", "45-6789 done"],
        ["Card 4111 1111 ", "1111 1111 done"],
    ],
)
def test_redacts_pii_split_across_chunks(parts):
    redactor = IncrementalRedactor()
    result = "".join(redactor.feed(part) for part in parts)
    result += redactor.feed("", final=True)

    assert "[REDACTED]" in result
    assert not any(value in result for value in ("alex@example.com", "123-45-6789", "4111 1111 1111 1111"))


def test_normal_text_passes_unchanged():
    text = "A normal response with no private information."
    assert redact(text) == text


def test_redactor_keeps_only_a_bounded_candidate_suffix():
    redactor = IncrementalRedactor()
    redactor.feed("a" * 2000)

    assert len(redactor.pending) <= 254


def test_sse_stream_redacts_pii_split_between_events():
    FakeClient.response = FakeResponse([
        sse("Contact alex@"),
        sse("example.com or call 123-"),
        sse("45-6789. Card 4111 1111 "),
        sse("1111 1111."),
    ])

    response = TestClient(gateway.app).post(
        "/generate", json={"prompt": "hello"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "[REDACTED]" in response.text
    assert "alex@example.com" not in response.text
    assert "123-45-6789" not in response.text
    assert "4111 1111 1111 1111" not in response.text


def test_done_marker_is_kept_as_a_separate_sse_event():
    FakeClient.response = FakeResponse([
        b'data: {"choices":[{"delta":{"content":"Contact "}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"alex@"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"example.com"}}]}\n\n',
        b"data: [DONE]\n\n",
    ])

    response = TestClient(gateway.app).post(
        "/generate", json={"prompt": "hello"}
    )
    events = [
        block
        for block in response.text.split("\n\n")
        if block
    ]

    assert events == [
        'data: {"choices":[{"delta":{"content":"Contact "}}]}',
        'data: {"choices":[{"delta":{"content":"[REDACTED]"}}]}',
        "data: [DONE]",
    ]
    assert "alex@example.com" not in response.text


def test_stream_yields_before_provider_is_exhausted():
    class SlowResponse(FakeResponse):
        async def aiter_bytes(self):
            self.chunks_read = 1
            yield sse("Hello, ")
            self.chunks_read = 2
            await asyncio.Event().wait()

    response = SlowResponse([])

    async def check_stream():
        iterator = gateway.stream_response(response)
        first = await asyncio.wait_for(iterator.__anext__(), timeout=1)
        assert b"Hello, " in first
        assert response.chunks_read == 1
        await iterator.aclose()

    asyncio.run(check_stream())


def test_connection_error_returns_clean_gateway_error():
    FakeClient.send_error = httpx.ConnectError("private connection details")

    response = TestClient(gateway.app).post("/generate", json={"prompt": "hello"})

    assert response.status_code == 502
    assert response.json() == {"error": "Provider request failed"}
    assert "private connection details" not in response.text
    assert FakeClient.closed


def test_provider_timeout_returns_clean_gateway_error():
    FakeClient.send_error = httpx.ReadTimeout("private timeout details")

    response = TestClient(gateway.app).post("/generate", json={"prompt": "hello"})

    assert response.status_code == 504
    assert response.json() == {"error": "Provider request timed out"}
    assert "private timeout details" not in response.text


def test_invalid_request_is_rejected():
    response = TestClient(gateway.app).post("/generate", json={"prompt": ""})
    assert response.status_code == 422


def test_mock_provider_streams_sse_independently():
    response = TestClient(mock_provider.app).post(
        "/generate", json={"prompt": "A test response"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "Mock response: A test response" in "".join(
        json.loads(line[6:])["text"]
        for line in response.text.splitlines()
        if line.startswith("data: ")
    )
