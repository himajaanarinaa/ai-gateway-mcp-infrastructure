# LLM model router

Install dependencies with `python -m pip install -r requirements.txt`.

Run providers and gateway in separate terminals:

```sh
uvicorn mock_providers:primary_app --port 8001
uvicorn mock_providers:backup_app --port 8002
uvicorn gateway:app --port 8000
```

Send a completion:

```sh
curl -H "X-API-Key: tenant-a" -H "Content-Type: application/json" \
  -d '{"prompt":"Write a short greeting"}' http://127.0.0.1:8000/complete
```

Each request costs `ceil(prompt character count / 4)` estimated tokens, with a minimum of one. This simple estimate is not a model tokenizer and can differ from actual usage. Input tokens are reserved before calling a provider, so admitted requests count even if providers fail. SQLite stores only a hash of each API key and usage records on disk. Set `RATE_LIMIT_DB` to change the database path.

The primary mock can return 429 or exceed the 3-second timeout by setting `PRIMARY_MODE=429` or `PRIMARY_MODE=slow` when starting it. Only a primary 429 or timeout triggers one backup attempt. Run tests with `pytest -q`.
