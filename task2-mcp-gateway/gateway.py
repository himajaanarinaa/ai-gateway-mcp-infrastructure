import json
import os
import re
from typing import Optional

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI()

DOWNSTREAM_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8001")
TIMEOUT_SECONDS = 5.0
TOKENS = {"admin-token": "admin", "viewer-token": "viewer"}
BEARER_PATTERN = re.compile(r"^Bearer ([^\s]+)$", re.IGNORECASE)


def rpc_error(request_id, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def request_id(payload) -> Optional[object]:
    if isinstance(payload, dict):
        return payload.get("id")
    return None


@app.post("/")
async def proxy(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    match = BEARER_PATTERN.fullmatch(authorization or "")
    token = match.group(1) if match else None
    role = TOKENS.get(token)
    if role is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(rpc_error(None, -32700, "Parse error"), status_code=400)

    if (
        not isinstance(payload, dict)
        or payload.get("jsonrpc") != "2.0"
        or not isinstance(payload.get("method"), str)
        or "id" not in payload
        or isinstance(payload["id"], bool)
        or not isinstance(payload["id"], (str, int, float, type(None)))
    ):
        return JSONResponse(rpc_error(request_id(payload), -32600, "Invalid Request"))

    method = payload["method"]
    if method not in ("tools/list", "tools/call"):
        return JSONResponse(rpc_error(payload["id"], -32601, "Method not found"))

    params = payload.get("params")
    if not isinstance(params, dict):
        return JSONResponse(rpc_error(payload["id"], -32602, "Invalid params"))

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return JSONResponse(rpc_error(payload["id"], -32602, "Invalid params"))
        if name.startswith("admin_") and role != "admin":
            return JSONResponse(rpc_error(payload["id"], -32001, "Unauthorized Tool Call"))

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            downstream = await client.post(
                DOWNSTREAM_URL,
                content=raw_body,
                headers={"Content-Type": "application/json"},
            )
        downstream.raise_for_status()
        return Response(
            content=downstream.content,
            status_code=downstream.status_code,
            media_type="application/json",
        )
    except httpx.TimeoutException:
        return JSONResponse(rpc_error(payload["id"], -32603, "Downstream request timed out"))
    except (httpx.HTTPError, ValueError):
        return JSONResponse(rpc_error(payload["id"], -32603, "Downstream request failed"))
