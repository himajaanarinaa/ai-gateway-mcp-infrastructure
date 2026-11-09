import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ErrorCode,
  ListToolsRequestSchema,
  McpError,
} from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";

const customerIdSchema = z.string().regex(/^CUST-[A-Z0-9]{5}$/);
const customerRecordInput = z.strictObject({
  customer_id: customerIdSchema,
});
const refundInput = z.strictObject({
  customer_id: customerIdSchema,
  amount: z.number().finite().positive(),
  reason: z.string().trim().min(10),
});
const customers = new Map([
  ["CUST-12345", { customer_id: "CUST-12345", name: "Sample Customer", status: "active" }],
]);

const tools = [
  {
    name: "get_customer_record",
    description: "Look up a customer by ID.",
    inputSchema: {
      type: "object" as const,
      properties: {
        customer_id: { type: "string", pattern: "^CUST-[A-Z0-9]{5}$" },
      },
      required: ["customer_id"],
      additionalProperties: false,
    },
  },
  {
    name: "trigger_refund",
    description: "Record a simulated refund request.",
    inputSchema: {
      type: "object" as const,
      properties: {
        customer_id: { type: "string", pattern: "^CUST-[A-Z0-9]{5}$" },
        amount: { type: "number", exclusiveMinimum: 0 },
        reason: { type: "string", minLength: 10 },
      },
      required: ["customer_id", "amount", "reason"],
      additionalProperties: false,
    },
  },
];

const server = new Server({
  name: "customer-tools",
  version: "1.0.0",
}, {
  capabilities: { tools: {} },
});

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  if (name === "get_customer_record") {
    const parsed = customerRecordInput.safeParse(args);
    if (!parsed.success) {
      throw new McpError(ErrorCode.InvalidParams, parsed.error.message);
    }

    const customer = customers.get(parsed.data.customer_id);
    return {
      content: [{
        type: "text",
        text: JSON.stringify(customer ?? { customer_id: parsed.data.customer_id, found: false }),
      }],
    };
  }

  if (name === "trigger_refund") {
    const parsed = refundInput.safeParse(args);
    if (!parsed.success) {
      throw new McpError(ErrorCode.InvalidParams, parsed.error.message);
    }

    return {
      content: [{
        type: "text",
        text: JSON.stringify({
          status: "accepted",
          simulated: true,
          ...parsed.data,
        }),
      }],
    };
  }

  throw new McpError(ErrorCode.InvalidParams, `Unknown tool: ${name}`);
});

const transport = new StdioServerTransport();

try {
  await server.connect(transport);
} catch (error) {
  console.error("Failed to start MCP server:", error);
  process.exitCode = 1;
}
