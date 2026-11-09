# Task 1: Customer tools MCP server

This server exposes `get_customer_record` and `trigger_refund` over MCP stdio.

## Run

```sh
npm install
npm start
```

`get_customer_record` accepts IDs matching `CUST-` followed by five uppercase letters or digits. `trigger_refund` also requires a finite amount greater than zero and a non-blank reason of at least 10 characters. Both tools reject extra input fields. The refund response is simulated and does not issue a payment.

The server validates every tool call with Zod and maps invalid arguments to JSON-RPC invalid-params errors. Protocol output uses stdout; startup errors are written to stderr.
