import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

type JsonRpcMessage = {
  id?: number;
  result?: {
    content?: Array<{ text?: string }>;
    tools?: Array<{ name: string }>;
  };
  error?: { code: number; message: string };
};

const serverPath = fileURLToPath(new URL("../src/server.ts", import.meta.url));
const child = spawn(process.execPath, ["--import", "tsx", serverPath], {
  stdio: ["pipe", "pipe", "pipe"],
});
const responses = new Map<number, (message: JsonRpcMessage) => void>();
let nextId = 1;
let outputError: Error | undefined;

const lines = createInterface({ input: child.stdout });
lines.on("line", (line) => {
  try {
    const message = JSON.parse(line) as JsonRpcMessage;
    if (typeof message.id === "number") {
      responses.get(message.id)?.(message);
      responses.delete(message.id);
    }
  } catch {
    outputError = new Error(`Non-JSON output on stdout: ${line}`);
  }
});

function request(method: string, params: unknown): Promise<JsonRpcMessage> {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      responses.delete(id);
      reject(new Error(`Timed out waiting for ${method}`));
    }, 3000);
    responses.set(id, (message) => {
      clearTimeout(timeout);
      resolve(message);
    });
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
  });
}

function callTool(name: string, args: Record<string, unknown>) {
  return request("tools/call", { name, arguments: args });
}

after(async () => {
  child.kill("SIGTERM");
  await once(child, "exit");
});

test("serves tools over stdio and rejects invalid tool arguments", async () => {
  const initialized = await request("initialize", {
    protocolVersion: "2025-03-26",
    capabilities: {},
    clientInfo: { name: "test-client", version: "1.0.0" },
  });
  assert.ok(initialized.result);
  child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" })}\n`);

  const listed = await request("tools/list", {});
  assert.deepEqual(
    listed.result?.tools?.map((tool) => tool.name).sort(),
    ["get_customer_record", "trigger_refund"],
  );

  const valid = await callTool("get_customer_record", { customer_id: "CUST-12345" });
  assert.equal(valid.error, undefined);
  assert.match(valid.result?.content?.[0]?.text ?? "", /"status":"active"/);

  const invalidCalls = [
    callTool("get_customer_record", { customer_id: "cust-12345" }),
    callTool("get_customer_record", { customer_id: "CUST-12345", extra: true }),
    callTool("trigger_refund", {
      customer_id: "CUST-12345",
      amount: 0,
      reason: "Customer changed their mind",
    }),
    callTool("trigger_refund", {
      customer_id: "CUST-12345",
      amount: 10,
      reason: "short",
    }),
  ];

  for (const response of await Promise.all(invalidCalls)) {
    assert.equal(response.error?.code, -32602);
  }
  assert.equal(outputError, undefined);
});
