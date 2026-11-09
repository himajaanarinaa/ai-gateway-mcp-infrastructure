# MCP security gateway

Install dependencies:

```sh
python -m pip install -r requirements.txt
```

Start the mock MCP server and gateway in separate terminals:

```sh
uvicorn mock_server:app --port 8001
uvicorn gateway:app --port 8000
```

Send a request through the gateway:

```sh
curl -H "Authorization: Bearer viewer-token" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  http://127.0.0.1:8000/
```

Use `admin-token` for `admin_reset_key`. Run tests with `pytest -q`.
