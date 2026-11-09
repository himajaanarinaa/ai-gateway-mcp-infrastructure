from fastapi import FastAPI

app = FastAPI()

TOOLS = [
    {
        "name": "get_customer_record",
        "description": "Get a customer record.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "admin_reset_key",
        "description": "Reset a customer's key.",
        "inputSchema": {"type": "object"},
    },
]


@app.post("/")
async def handle_rpc(payload: dict):
    method = payload.get("method")
    request_id = payload.get("id")

    if method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = payload.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "Invalid params"},
            }
        name = params["name"]
        result = {"content": [{"type": "text", "text": f"{name} completed"}]}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }

    return {"jsonrpc": "2.0", "id": request_id, "result": result}
