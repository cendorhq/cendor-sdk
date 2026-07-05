# Ecosystem & interop

> Phase 3. Make governed `cendor-sdk` agents first-class citizens elsewhere: consume **MCP** tools,
> serve over **A2A**, publish as a **Microsoft 365 / Foundry** custom-engine agent, emit a full-run
> **OpenTelemetry** span tree, and wire **human-in-the-loop** approvals into the audit chain.

Everything here is optional and local-first. Provider/protocol SDKs are extras; the telemetry is a
no-op unless OpenTelemetry is installed and configured.

## MCP — consume Model Context Protocol tools

`load_mcp_tools(session)` turns an MCP server's tools into governed `Tool`s (schema comes from the
server). MCP is async, so use them with `run.aio(...)`. Install the client with `pip install
"cendor-sdk[mcp]"`.

```python
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from cendor.sdk import Agent, run, load_mcp_tools

params = StdioServerParameters(command="my-mcp-server")
async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
    await session.initialize()
    tools = await load_mcp_tools(session)          # each MCP tool -> a governed Tool
    agent = Agent(name="assistant", model="gpt-4o", tools=tools, instructions="Use the tools.")
    result = await run.aio(agent, "…")             # MCP calls flow through the bus/audit/budget
```

The integration is duck-typed against a session with async `list_tools()` / `call_tool(name, args)`,
so it's easy to test with a fake session (see `tests/test_interop.py`).
`load_mcp_resources(session)` reads resources into `{uri: contents}`.

## A2A — serve an agent over the Agent-to-Agent protocol

```python
from cendor.sdk import Agent, A2AServer, A2AClient

agent  = Agent(name="greeter", model="gpt-4o", instructions="Greet.")
server = A2AServer(agent)

# In-process (offline / embedded):
client = A2AClient(server)
print(client.card())               # the A2A agent card (name, skills, IO modes)
print(client.send("hi"))           # -> the agent's reply
full = client.send_full("hi")      # full A2A message result incl. governance metadata:
                                   #   {"trace_id": ..., "cost_usd": ..., "agents": [...]}

# Over local HTTP (optional, opt-in — stdlib only):
from cendor.sdk.a2a import serve
httpd = serve(agent, host="127.0.0.1", port=8080)   # GET /.well-known/agent-card.json ; POST /
# httpd.serve_forever()  (run in a thread; httpd.shutdown() to stop)
```

## Microsoft 365 / Foundry — publish as a custom-engine agent

`FoundryAdapter` speaks the Bot Framework **Activity** protocol, the surface a custom-engine agent
exposes to Copilot / Teams / Azure AI Foundry.

```python
from cendor.sdk import Agent, FoundryAdapter

adapter = FoundryAdapter(Agent(name="assistant", model="gpt-4o", instructions="Help."))

# In your web endpoint:
reply = adapter.on_activity(incoming_activity)   # -> outgoing message Activity, or None (non-message)
# reply["channelData"]["cendor"] carries {"trace_id", "cost_usd", "agents"}
adapter.manifest()                                # minimal registration manifest
```

## OpenTelemetry — a full-run gen_ai span tree

`span_tree(result)` emits a `gen_ai.*` span tree for a completed run so the whole trajectory shows
up in Foundry / Datadog / Jaeger: a root `agent.run`, a child per agent segment, and a grandchild
per model call (`chat {model}`) and tool execution (`execute_tool {name}`). It uses OpenTelemetry
directly (extra `[otel]`) and is a **no-op returning `False`** if OTel isn't installed.

```python
from cendor.sdk import run
from cendor.sdk.otel import span_tree

result = run(agent, "…")
span_tree(result)     # spans exported to your configured OTel pipeline; mirrors result's tree
```

Spans carry `gen_ai.request.model`, `gen_ai.system`, `gen_ai.usage.input_tokens` /
`output_tokens`, `gen_ai.usage.cost`, and per-agent `gen_ai.agent.name`.

## Human-in-the-loop — approvals in the audit chain

`acttrace` records *that* oversight happened; the pause/approve/resume is your app's job.
`require_approval` wraps a tool so each call consults an `approver` (the pause) and records the
verdict via `decision.human_oversight(...)` on the **same audit chain** the run is correlated by.

```python
from cendor.sdk import Agent, run, AuditLog
from cendor.sdk.hitl import require_approval

def approver(tool_name, args):
    return args["amount"] < 100, "auto-approved under $100"   # -> (approved, note)

refund = require_approval(issue_refund, approver=approver, reviewer="ops@bank")
agent  = Agent(name="support", model="gpt-4o", tools=[refund], instructions="Use tools.")

log = AuditLog(system="support", risk_tier="high", path="audit.jsonl")
run(agent, "Refund order 42 for $50", audit=log)
# audit.jsonl gains a human_oversight entry (approved/rejected), hash-chained & verify()-able.
# On rejection the real tool never runs and the model gets a denial to react to.
```

In production the `approver` is where you block on a reviewer — a prompt, a queue, a webhook — then
return the decision to resume (or deny) the run.
