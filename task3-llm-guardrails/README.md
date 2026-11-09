# LLM streaming guardrail

Install dependencies:

```sh
python -m pip install -r requirements.txt
```

Run the mock provider and gateway in separate terminals:

```sh
uvicorn mock_provider:app --port 8002
uvicorn gateway:app --port 8000
```

Send a generation request:

```sh
curl -N -H "Content-Type: application/json" \
  -d '{"prompt":"Contact jane@example.com"}' \
  http://127.0.0.1:8000/generate
```

Run tests with `pytest -q`.
