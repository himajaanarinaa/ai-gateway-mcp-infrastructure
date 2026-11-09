import asyncio
import os
import sqlite3
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rate_limiter import SQLiteRateLimiter

app = FastAPI()
PRIMARY_URL = os.getenv("PRIMARY_PROVIDER_URL", "http://127.0.0.1:8001/complete")
BACKUP_URL = os.getenv("BACKUP_PROVIDER_URL", "http://127.0.0.1:8002/complete")
PRIMARY_TIMEOUT = 3.0
BACKUP_TIMEOUT = 10.0
DATABASE_PATH = os.getenv(
    "RATE_LIMIT_DB",
    os.path.join(os.path.dirname(__file__), "rate_limits.sqlite3"),
)
_limiter: Optional[SQLiteRateLimiter] = None


class CompletionRequest(BaseModel):
    prompt: str = Field(min_length=1)


def error_response(
    code: str,
    message: str,
    status_code: int,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status_code,
        headers=headers,
    )


def get_limiter() -> SQLiteRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = SQLiteRateLimiter(DATABASE_PATH)
    return _limiter


@app.exception_handler(RequestValidationError)
async def invalid_request_handler(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    return error_response("INVALID_REQUEST", "Invalid completion request", 422)


async def post_to_provider(
    url: str,
    payload: Dict[str, Any],
    timeout: float,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await asyncio.wait_for(
            client.post(url, json=payload),
            timeout=timeout,
        )


async def try_provider(
    url: str,
    payload: Dict[str, Any],
    timeout: float,
) -> Tuple[Optional[httpx.Response], bool]:
    try:
        return await post_to_provider(url, payload, timeout), False
    except (httpx.TimeoutException, asyncio.TimeoutError):
        return None, True
    except httpx.HTTPError:
        return None, False


def provider_result(response: httpx.Response) -> Optional[Any]:
    if not 200 <= response.status_code < 300:
        return None
    try:
        return response.json()
    except (ValueError, TypeError):
        return None


@app.post("/complete")
async def complete(
    body: CompletionRequest,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    if not x_api_key or not x_api_key.strip():
        return error_response("INVALID_API_KEY", "A valid API key is required", 401)

    tokens = SQLiteRateLimiter.estimate_tokens(body.prompt)
    try:
        allowed, _ = await asyncio.to_thread(
            get_limiter().consume,
            x_api_key,
            tokens,
        )
    except sqlite3.Error:
        return error_response(
            "RATE_LIMITER_UNAVAILABLE",
            "Unable to process the request",
            503,
        )

    if not allowed:
        return error_response(
            "RATE_LIMIT_EXCEEDED",
            "Token limit exceeded",
            429,
            headers={"Retry-After": "60"},
        )

    payload = body.model_dump()
    primary, timed_out = await try_provider(
        PRIMARY_URL,
        payload,
        PRIMARY_TIMEOUT,
    )

    if primary is not None and primary.status_code != 429:
        result = provider_result(primary)
        if result is not None:
            return JSONResponse(result)
        return error_response(
            "PROVIDER_UNAVAILABLE",
            "Unable to complete the request",
            503,
        )
    if primary is None and not timed_out:
        return error_response(
            "PROVIDER_UNAVAILABLE",
            "Unable to complete the request",
            503,
        )

    backup, _ = await try_provider(BACKUP_URL, payload, BACKUP_TIMEOUT)
    if backup is not None:
        result = provider_result(backup)
        if result is not None:
            return JSONResponse(result)

    return error_response(
        "PROVIDER_UNAVAILABLE",
        "Unable to complete the request",
        503,
    )
