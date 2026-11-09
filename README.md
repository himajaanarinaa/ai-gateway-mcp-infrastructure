# AI Gateway and MCP Infrastructure

I built these four small projects as practical examples of MCP servers and LLM gateway features. Each task is self-contained, uses a mock service where needed, and has its own setup instructions and tests.

## Task 1: MCP server

I implemented a TypeScript MCP server over stdio with two tools: `get_customer_record` and `trigger_refund`. Zod validates their inputs, including the customer ID format, positive refund amount, and reason length. Invalid arguments are returned as JSON-RPC invalid-parameter errors. The customer record is sample data and refunds are simulated.

## Task 2: MCP security gateway

I built a FastAPI proxy for a mock MCP server. The gateway authenticates bearer tokens as either admin or viewer, forwards valid `tools/list` and `tools/call` requests, and blocks viewers from calling tools prefixed with `admin_`. Tests check request forwarding, permissions, and that unauthorized calls never reach the mock server.

## Task 3: Streaming PII guardrail

I built a FastAPI gateway that forwards generation requests to a mock provider and streams its SSE response. It redacts email addresses, US Social Security numbers, and credit card numbers. A bounded rolling buffer handles sensitive values split across events while allowing safe text to continue streaming. Tests cover redaction, split values, provider errors, and streaming before the full response is collected.

## Task 4: Rate limiting and model fallback

I implemented a completion gateway that estimates prompt tokens and records per-API-key usage in SQLite. It enforces a 50,000-token limit per rolling 60-second window using a transaction to keep concurrent reservations within the limit. The gateway tries a backup provider once when the primary returns HTTP 429 or times out after three seconds. The token estimate is `ceil(prompt character count / 4)`, not a model-specific tokenizer; admitted requests count even when a provider fails.

## Running and testing

Each task has a README with its setup and run commands. Run tests from the corresponding task directory:

```sh
# Task 1
cd task1-mcp-server
npm install
npm run build
npm test

# Tasks 2, 3, or 4: enter the task directory first
python -m pip install -r requirements.txt
python -m pytest -q
```

The HTTP gateways and their mock providers are separate apps and can be run in separate terminals. All providers use mock responses; these projects do not call production services or perform real refunds.
