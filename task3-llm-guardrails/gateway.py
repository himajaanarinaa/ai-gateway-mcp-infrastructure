import codecs
import json
import os
from typing import Any, AsyncIterator, List, Optional, Tuple

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from guardrails import IncrementalRedactor

app = FastAPI()
PROVIDER_URL = os.getenv("LLM_PROVIDER_URL", "http://127.0.0.1:8002/generate")
TIMEOUT = httpx.Timeout(10.0)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)


class SSEParser:
    def __init__(self) -> None:
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buffer = ""
        self.lines: List[str] = []

    def _line(self, line: str) -> Optional[List[str]]:
        line = line.removesuffix("\r")
        if line:
            self.lines.append(line)
            return None
        if not self.lines:
            return None
        frame, self.lines = self.lines, []
        return frame

    def feed(self, chunk: bytes, final: bool = False) -> List[List[str]]:
        self.buffer += self.decoder.decode(chunk, final=final)
        frames = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            frame = self._line(line)
            if frame:
                frames.append(frame)
        if final:
            if self.buffer:
                frame = self._line(self.buffer)
                if frame:
                    frames.append(frame)
                self.buffer = ""
            frame = self._line("")
            if frame:
                frames.append(frame)
        return frames


def _set_path(value: Any, path: Tuple[Any, ...], replacement: str) -> None:
    current = value
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = replacement


def _content_paths(value: Any, path: Tuple[Any, ...] = ()) -> List[Tuple[Any, ...]]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (key,)
            if key in ("text", "content") and isinstance(child, str):
                found.append(child_path)
            else:
                found.extend(_content_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_content_paths(child, path + (index,)))
    return found


def _data_values(frame: List[str]) -> List[str]:
    return [line[5:].removeprefix(" ") if line.startswith("data:") else "" for line in frame if line.startswith("data:")]


def _encode_frame(headers: List[str], data: str) -> bytes:
    lines = [line for line in headers if not line.startswith("data:")]
    lines.append(f"data: {data}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


class SSEGuard:
    def __init__(self) -> None:
        self.redactor = IncrementalRedactor()
        self.last_template: Optional[Tuple[Any, Optional[Tuple[Any, ...]], List[str]]] = None

    def process(self, frame: List[str]) -> Optional[bytes]:
        values = _data_values(frame)
        if not values:
            return ("\n".join(frame) + "\n\n").encode("utf-8")

        raw_data = "\n".join(values)
        headers = [line for line in frame if not line.startswith("data:")]
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError:
            safe_text = self.redactor.feed(raw_data)
            self.last_template = (None, None, headers)
            return _encode_frame(headers, safe_text) if safe_text else None

        paths = _content_paths(payload)
        if not paths:
            return ("\n".join(frame) + "\n\n").encode("utf-8")

        parts = []
        for path in paths:
            current = payload
            for part in path:
                current = current[part]
            safe_text = self.redactor.feed(current)
            _set_path(payload, path, safe_text)
            parts.append(safe_text)
            self.last_template = (payload, path, headers)

        if not any(parts):
            return None
        return _encode_frame(headers, json.dumps(payload, separators=(",", ":")))

    def finish(self) -> Optional[bytes]:
        tail = self.redactor.feed("", final=True)
        if not tail or self.last_template is None:
            return None
        payload, path, headers = self.last_template
        if path is None:
            return _encode_frame(headers, tail)
        _set_path(payload, path, tail)
        return _encode_frame(headers, json.dumps(payload, separators=(",", ":")))


async def stream_response(response: httpx.Response) -> AsyncIterator[bytes]:
    parser = SSEParser()
    guard = SSEGuard()
    try:
        async for chunk in response.aiter_bytes():
            for frame in parser.feed(chunk):
                output = guard.process(frame)
                if output:
                    yield output
        for frame in parser.feed(b"", final=True):
            output = guard.process(frame)
            if output:
                yield output
        tail = guard.finish()
        if tail:
            yield tail
    except httpx.TimeoutException:
        yield b'event: error\ndata: {"error":"Provider request timed out"}\n\n'
    except httpx.HTTPError:
        yield b'event: error\ndata: {"error":"Provider stream failed"}\n\n'


async def close_provider(response: httpx.Response, client: httpx.AsyncClient) -> None:
    await response.aclose()
    await client.aclose()


@app.post("/generate")
async def generate(body: GenerateRequest):
    client = httpx.AsyncClient(timeout=TIMEOUT)
    request = client.build_request("POST", PROVIDER_URL, json=body.model_dump())
    try:
        response = await client.send(request, stream=True)
        response.raise_for_status()
    except httpx.TimeoutException:
        await client.aclose()
        return JSONResponse({"error": "Provider request timed out"}, status_code=504)
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"error": "Provider request failed"}, status_code=502)

    return StreamingResponse(
        stream_response(response),
        media_type="text/event-stream",
        background=BackgroundTask(close_provider, response, client),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
