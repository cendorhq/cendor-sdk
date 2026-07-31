# Providers

One canonical message shape, ten provider paths: OpenAI (Chat Completions + Responses),
Anthropic, Google Gemini, AWS Bedrock, Ollama, Hugging Face, Azure AI Foundry (Chat + Responses),
and Foundry Local. Switch providers by changing the model id — the conversation, tools, and
governance don't change.

## The support matrix

| Provider | Install extra | Response normalization | Tool formatting |
|---|---|---|---|
| OpenAI (Chat Completions) | `[openai]` | ✅ | ✅ functions |
| OpenAI (Responses API) | `[openai]` | ✅ | ✅ functions |
| Anthropic (Messages) | `[anthropic]` | ✅ | ✅ tools |
| Google Gemini | `[google]` | ✅ | ✅ function declarations |
| AWS Bedrock | `[bedrock]` | ✅ | ✅ toolConfig |
| Ollama (local) | `[ollama]` | ✅ | ✅ functions |
| Hugging Face | `[huggingface]` | ✅ (OpenAI-shape) | ✅ functions |
| Azure AI Foundry (Chat) | `[azure]` | ✅ (OpenAI-shape) | ✅ functions |
| Azure AI Foundry (Responses) | `[azure]` | ✅ (OpenAI-shape) | ✅ functions |
| Foundry Local (on-device) | `[foundry-local]` | ✅ (OpenAI-shape) | ✅ functions |

**Auth for every path:** see [API keys & credentials](#api-keys--credentials) — the SDK reads
the provider's standard env var (or takes `api_key=` / a pre-built `client=`); there is no
Cendor-specific key setting.

Normalization is isolated in `providers.py` (Python) / `providers.ts` (TypeScript) with
per-provider fixtures, so response-shape drift is contained. **All ten paths ship in `@cendor/sdk`**
behind the same `Provider` seam — Hugging Face, Ollama, Gemini, and Bedrock drive the model with
end-to-end token/cost capture in TypeScript today, via `@cendor/core`'s provider detection. See the
[parity matrix](/docs/languages).

**Async (`run.aio`)** runs natively for OpenAI (Chat + Responses), Anthropic, Ollama, Hugging Face,
and **Google Gemini** (google-genai's `aio.models.generate_content`). **AWS Bedrock** (boto3) has no
async client, so since cendor-sdk 1.14 `run.aio` offloads the blocking `converse` call to a worker
thread (`asyncio.to_thread`) — the event loop keeps running and the run's governance scope still
attaches (contextvars propagate into the thread).

## Core concepts

### API keys & credentials

The SDK builds the provider client for you, so there is **no Cendor key config**. Credentials
resolve in this order — the same in both languages:

1. **Explicit:** `Agent(api_key=…)` (Python) / `new Agent({ apiKey: … })` (TypeScript).
2. **The provider's standard env var** — the same variable the provider's own SDK reads:

| Provider | Key | Endpoint / region |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `base_url=` for gateways |
| Anthropic | `ANTHROPIC_API_KEY` | — |
| Google Gemini | `GOOGLE_API_KEY` | — |
| AWS Bedrock | AWS credential chain (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, profile, or IAM role) — no API key | `AWS_REGION` |
| Ollama | none — local | `OLLAMA_HOST` |
| Hugging Face | `HF_TOKEN` (or `HUGGINGFACEHUB_API_TOKEN`) | — |
| Azure AI Foundry | `AZURE_OPENAI_API_KEY` / `AZURE_INFERENCE_CREDENTIAL` / `AZURE_AI_API_KEY` | `AZURE_OPENAI_ENDPOINT` |
| Foundry Local | none (`FOUNDRY_LOCAL_API_KEY` optional) | `FOUNDRY_LOCAL_ENDPOINT` |

3. **Bring your own client:** `Agent(client=…)` — you construct the provider SDK client yourself
   (with whatever auth it supports); Cendor instruments it on adoption, so budgets, guardrails,
   and audit still apply.

The SDK never reads `.env` files — load them yourself (`python-dotenv`, `node --env-file=.env`).
If no key is found, the client is still constructed with a placeholder so **keyless flows work**
(cassette replay, pre-flight budget blocks) — a live call then fails with the provider's own
authentication error. From **cendor-sdk 1.6.2 / @cendor/sdk 0.9.2** that failure is caught and
re-raised as a `MissingAPIKeyError` that names the env var to set and links back here (older
versions surface the provider's bare 401 mentioning `cendor-sdk-placeholder` — set the env var
above or pass `api_key=`).

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import Agent

# 1) Recommended — the standard env var; nothing to pass:
#      export OPENAI_API_KEY="sk-..."           # bash
#      $env:OPENAI_API_KEY = "sk-..."           # PowerShell
agent = Agent(name="a", model="gpt-4o")

# 2) Explicit — e.g. keys from a secret manager (never surfaced in repr/logs):
agent = Agent(name="a", model="gpt-4o", api_key=read_secret("openai"))

# 3) Bring your own client — full control over auth, still governed:
from openai import OpenAI
agent = Agent(name="a", model="gpt-4o", client=OpenAI(api_key=read_secret("openai")))
```

<!-- tab: TypeScript -->

```ts
import { Agent } from '@cendor/sdk';
import OpenAI from 'openai';

// 1) Recommended — the standard env var (process.env.OPENAI_API_KEY); nothing to pass:
const a = new Agent({ name: 'a', model: 'gpt-4o' });

// 2) Explicit — e.g. keys from a secret manager:
const b = new Agent({ name: 'a', model: 'gpt-4o', apiKey: readSecret('openai') });

// 3) Bring your own client — full control over auth, still governed:
const c = new Agent({ name: 'a', model: 'gpt-4o', client: new OpenAI({ apiKey: readSecret('openai') }) });
```

<!-- /tabs -->

Per-provider notes:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
# Anthropic — ANTHROPIC_API_KEY, or api_key=
claude = Agent(name="c", model="claude-sonnet-5", api_key="sk-ant-...")

# Google Gemini — GOOGLE_API_KEY, or api_key=
gem = Agent(name="g", model="gemini-2.5-flash")

# AWS Bedrock — NO api_key: standard AWS credentials + AWS_REGION
#   export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_REGION=us-east-1
br = Agent(name="b", model="anthropic.claude-sonnet-4-5-20250929-v1:0", provider="bedrock")

# Ollama — local, no key; remote host via OLLAMA_HOST
llama = Agent(name="l", model="llama3.2")

# Hugging Face — HF_TOKEN, or api_key=; always provider="huggingface"
hf = Agent(name="h", model="meta-llama/Llama-3.3-70B-Instruct", provider="huggingface")

# Azure AI Foundry — resource key + endpoint; always provider="azure"
az = Agent(name="z", model="my-gpt4o-deployment", provider="azure",
           api_key="<resource-key>", base_url="https://my-res.openai.azure.com")
```

<!-- tab: TypeScript -->

```ts
// Anthropic — ANTHROPIC_API_KEY, or apiKey:
const claude = new Agent({ name: 'c', model: 'claude-sonnet-5', apiKey: 'sk-ant-...' });

// Google Gemini — GOOGLE_API_KEY, or apiKey:
const gem = new Agent({ name: 'g', model: 'gemini-2.5-flash' });

// AWS Bedrock — NO apiKey: standard AWS credentials + AWS_REGION
const br = new Agent({ name: 'b', model: 'anthropic.claude-sonnet-4-5-20250929-v1:0', provider: 'bedrock' });

// Ollama — local, no key; remote host via baseURL
const llama = new Agent({ name: 'l', model: 'llama3.2' });

// Hugging Face — HF_TOKEN, or apiKey; always provider: 'huggingface'
const hf = new Agent({ name: 'h', model: 'meta-llama/Llama-3.3-70B-Instruct', provider: 'huggingface' });

// Azure AI Foundry — resource key + endpoint; always provider: 'azure'
const az = new Agent({ name: 'z', model: 'my-gpt4o-deployment', provider: 'azure',
                       apiKey: '<resource-key>', baseURL: 'https://my-res.openai.azure.com' });
```

<!-- /tabs -->

The same `api_key` / `provider` / `base_url` options work on [`llm_summarizer`](memory.md),
[`embed`](rag.md), and the RAG retriever. Azure **keyless** (Entra ID) auth: see
[Azure AI Foundry](#azure-ai-foundry). Testing **without** any key: see
[Eval & regression testing](eval.md) — cassette replay needs no credentials.

### Provider inference — and when to be explicit

The provider is inferred from the model id: `gpt-*`/`o*` → OpenAI, `claude-*` → Anthropic,
`gemini-*` → Google, and so on. Two families are **not** prefix-inferable — Hugging Face Hub ids
and Azure deployment names are arbitrary strings — so those always take `provider=` explicitly
(sections below).

### Provider params & reasoning — `Agent.extra`

`Agent.extra` merges arbitrary request kwargs into every model call — the passthrough for
anything the SDK doesn't model first-class: `tool_choice`, `reasoning_effort`, `top_p`, `stop`,
`seed`, `response_format`, `extra_body`. o-series models that reject `temperature` have it
omitted automatically.

<!-- tabs: lang -->
<!-- tab: Python -->

```python
agent = Agent(name="a", model="o3-mini", extra={"reasoning_effort": "high"})
picky = Agent(name="p", model="gpt-4o", tools=[...], extra={"tool_choice": "required"})
```

<!-- tab: TypeScript -->

```ts
const agent = new Agent({ name: 'a', model: 'o3-mini', extra: { reasoning_effort: 'high' } });
const picky = new Agent({ name: 'p', model: 'gpt-4o', tools: [/* ... */],
                          extra: { tool_choice: 'required' } });
// extra keys are raw provider request kwargs, so they stay snake_case in both languages
```

<!-- /tabs -->

### Pricing unpriced models

Model ids absent from the bundled price table (Hub ids, deployment names, custom gateways) cost
`$0`, so a USD [`budget(...)`](governance.md#budgets) can't bind. Register a rate and cost +
budgets work.

**For an Azure/Foundry deployment, you usually don't need the rate card** — you know which model the
deployment serves, and that is enough:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import register_deployment

register_deployment("prod-gpt4o-eastus", like="gpt-4o")   # priced like gpt-4o from here on
```

<!-- tab: TypeScript -->

```ts
import { registerDeployment } from '@cendor/sdk';

registerDeployment('prod-gpt4o-eastus', { like: 'gpt-4o' }); // priced like gpt-4o from here on
```

<!-- /tabs -->

It copies `like`'s rates at registration and survives a `prices.refresh()`; an unknown `like` raises
rather than leaving the deployment quietly unpriced. Nothing is inferred from the deployment's name —
Cendor never guesses a price from an id's shape. When you *do* have the rate card:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import register_model_price, budget

register_model_price("my-gpt4o-deployment", input=2.50, output=10.00)  # USD per 1M tokens
with budget(usd=0.10, on_exceed="block"):
    run(agent, "...")
```

<!-- tab: TypeScript -->

```ts
import { registerModelPrice, withBudget } from '@cendor/sdk';

registerModelPrice('my-gpt4o-deployment', { input: 2.50, output: 10.00 });  // USD per 1M tokens
await withBudget({ usd: 0.10, onExceed: 'block' }, () =>
  run(agent, '...'));
```

<!-- /tabs -->

`register_model_price` and `configure(on_unpriced=…)` are [tokenguard](/docs/tokenguard)'s price
table, re-exported through the SDK — the same rates that price every `Result.cost` and bind every
`budget()`. Or use a `tokens=` cap: tokens are counted whether or not the model is priced, so a
token cap binds on a deployment name that a USD cap cannot.

#### Fail closed instead of costing `$0`

The default is `on_unpriced="warn"` — one `UnpricedModelWarning` per model, and **the call
proceeds**. That is the right default for exploration and the wrong one for a hard spend ceiling: a
typo in a deployment name silently prices at `$0`, and a USD cap never bites. Switch it once, at
startup, and `on_exceed="block"` refuses what it cannot price:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import configure, budget, run

configure(on_unpriced="raise")  # a USD cap now refuses an unpriced model instead of billing $0

with budget(usd=0.10, on_exceed="block"):
    run(agent, "...")           # raises BudgetExceeded pre-flight if the model has no price
```

<!-- tab: TypeScript -->

```ts
import { configure, withBudget, run } from '@cendor/sdk';

configure({ onUnpriced: 'raise' }); // a USD cap now refuses an unpriced model instead of billing $0

await withBudget({ usd: 0.1, onExceed: 'block' }, () =>
  run(agent, '...'));              // rejects pre-flight if the model has no price
```

<!-- /tabs -->

Either way the unpriced calls are still counted: `report()` (re-exported by the SDK) flags them
per row, and [tokenguard](/docs/tokenguard)'s own `unpriced_calls()` / `unpricedCalls()` returns the
total — so `"warn"` is observable rather than invisible. `"raise"` only changes what a **USD** cap
does; a `tokens=` cap is unaffected, because token counting never needed a price.

## Hugging Face

> **TypeScript usage capture.** All three providers below ship in `@cendor/sdk` with end-to-end
> cost and usage capture in TypeScript today — Azure AI Foundry and Foundry Local wrap the standard
> `openai` client, and Hugging Face is one of the providers `@cendor/core` detects directly. See the
> [parity matrix](/docs/languages).

Uses `huggingface_hub.InferenceClient.chat_completion` — the response is OpenAI-shaped, and the
call is attributed to `huggingface`, not `openai`. The `model` is a Hub id or an Inference
Endpoint URL. Install with `pip install "cendor-sdk[huggingface]"` (Python); in TypeScript add the
client peer with `npm i @cendor/sdk @huggingface/inference`.

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import Agent, run

# Serverless Inference API — token from api_key= or the HF_TOKEN / HUGGINGFACEHUB_API_TOKEN env var
agent = Agent(
    name="hf",
    model="meta-llama/Llama-3.1-8B-Instruct",
    provider="huggingface",        # required — Hub ids aren't prefix-inferable
    api_key="hf_...",              # optional; falls back to HF_TOKEN
)
result = run(agent, "Summarize the plot of Hamlet in two lines.")

# A dedicated Inference Endpoint (or a third-party provider) — point base_url at it:
agent = Agent(name="hf", model="tgi", provider="huggingface",
              base_url="https://<your-endpoint>.endpoints.huggingface.cloud")
```

<!-- tab: TypeScript -->

```ts
import { Agent, run } from '@cendor/sdk';

// Serverless Inference API — token from apiKey= or the HF_TOKEN / HUGGINGFACEHUB_API_TOKEN env var
const agent = new Agent({
  name: 'hf',
  model: 'meta-llama/Llama-3.1-8B-Instruct',
  provider: 'huggingface',        // required — Hub ids aren't prefix-inferable
  apiKey: 'hf_...',               // optional; falls back to HF_TOKEN
});
const result = await run(agent, 'Summarize the plot of Hamlet in two lines.');

// A dedicated Inference Endpoint (or a third-party provider) — point baseURL at it:
const endpoint = new Agent({ name: 'hf', model: 'tgi', provider: 'huggingface',
  baseURL: 'https://<your-endpoint>.endpoints.huggingface.cloud' });
```

<!-- /tabs -->

Route through a specific inference provider (e.g. `together`, `fireworks-ai`) with the
`HF_PROVIDER` env var.

## Azure AI Foundry

Microsoft's current guidance — the `AzureOpenAI` client and `azure-ai-inference` are being
retired — is to consume Foundry deployments with the **standard `openai` SDK** pointed at the
Foundry `/openai/v1/` endpoint. So `provider="azure"` *is* the OpenAI provider with Foundry-aware
construction. Install with `pip install "cendor-sdk[azure]"` (it just pulls `openai`); in TypeScript
the Azure path reuses the `openai` peer you already have. Two rules:

1. **`model` is your deployment name**, not the underlying model name (Azure keys on deployment).
2. **`base_url` is the Foundry endpoint** — `/openai/v1/` is appended for you; also read from
   `AZURE_OPENAI_ENDPOINT`. No `api-version` needed (the v1 GA API infers it). All three forms
   work: the Azure OpenAI host (`https://<res>.openai.azure.com`), the Foundry services host
   (`https://<res>.services.ai.azure.com`), and the **project endpoint** the portal shows
   (`https://<res>.services.ai.azure.com/api/projects/<name>`).

> **Reasoning-family deployments need `max_completion_tokens`, and you don't have to care.** A
> `gpt-5`/`o*` deployment rejects `max_tokens` with
> *"Unsupported parameter: 'max_tokens' … Use 'max_completion_tokens' instead."* Because the id a
> call carries is your **deployment** name, no library can predict this from the model string — so
> the SDK reads the provider's own message and re-issues the call once with the rename
> (`cendor-sdk` ≥ 1.21.0 / `@cendor/sdk` ≥ 3.1.0). `Agent(max_tokens=…)` is the one knob either way.

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import Agent, run, budget

# Azure OpenAI models:      https://<res>.openai.azure.com
# Foundry Models (DeepSeek, Grok, Llama, …): https://<res>.services.ai.azure.com — both work
agent = Agent(
    name="foundry",
    model="my-gpt4o-deployment",       # your Foundry DEPLOYMENT name
    provider="azure",
    base_url="https://my-resource.openai.azure.com",   # or AZURE_OPENAI_ENDPOINT
    api_key="<resource-key>",          # or AZURE_OPENAI_API_KEY / AZURE_INFERENCE_CREDENTIAL / AZURE_AI_API_KEY
)

with budget(usd=0.10, on_exceed="block"):
    result = run(agent, "Draft a one-line release note.")
```

<!-- tab: TypeScript -->

```ts
import { Agent, run, withBudget } from '@cendor/sdk';

// Azure OpenAI models:      https://<res>.openai.azure.com
// Foundry Models (DeepSeek, Grok, Llama, …): https://<res>.services.ai.azure.com — both work
const agent = new Agent({
  name: 'foundry',
  model: 'my-gpt4o-deployment',       // your Foundry DEPLOYMENT name
  provider: 'azure',
  baseURL: 'https://my-resource.openai.azure.com',   // or AZURE_OPENAI_ENDPOINT
  apiKey: '<resource-key>',           // or AZURE_OPENAI_API_KEY / AZURE_INFERENCE_CREDENTIAL / AZURE_AI_API_KEY
});

const result = await withBudget({ usd: 0.10, onExceed: 'block' }, () =>
  run(agent, 'Draft a one-line release note.'));
```

<!-- /tabs -->

**Microsoft Entra ID (keyless):** pass a refreshing bearer-token provider — Python takes it as
`api_key` (the v1 client refreshes it), TypeScript as `azureADTokenProvider` — or build the client
yourself and hand it to `Agent(client=...)`:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

token = get_bearer_token_provider(DefaultAzureCredential(), "https://ai.azure.com/.default")
client = OpenAI(base_url="https://my-resource.openai.azure.com/openai/v1/", api_key=token)
agent = Agent(name="foundry", model="my-gpt4o-deployment", provider="azure", client=client)
```

<!-- tab: TypeScript -->

```ts
import { Agent, run } from '@cendor/sdk';

// A refreshing Entra-ID bearer-token provider — e.g. @azure/identity's
// getBearerTokenProvider(new DefaultAzureCredential(), 'https://cognitiveservices.azure.com/.default')
const azureADTokenProvider = async () => '<entra-id-bearer-token>';

const agent = new Agent({
  name: 'foundry',
  model: 'my-gpt4o-deployment',        // your Foundry DEPLOYMENT name
  provider: 'azure',
  baseURL: 'https://my-resource.openai.azure.com',   // or AZURE_OPENAI_ENDPOINT
  azureADTokenProvider,                 // keyless — the token is refreshed on every request
});

const result = await run(agent, 'Draft a one-line release note.');
```

<!-- /tabs -->

### With the Foundry SDK (`azure-ai-projects` / `@azure/ai-projects`)

If your app already builds an `AIProjectClient`, hand its OpenAI client straight to the agent —
`get_openai_client()` / `getOpenAIClient()` returns a **plain `OpenAI` client** pointed at
`<endpoint>/openai/v1`, which is exactly the shape this provider builds and `cendor-core`'s
`instrument()` detects. Nothing else changes: budgets, guardrails and the audit chain ride the same
seams.

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from cendor.sdk import Agent, run

project = AIProjectClient(
    endpoint="https://my-resource.services.ai.azure.com/api/projects/my-project",
    credential=DefaultAzureCredential(),
)
agent = Agent(
    name="foundry",
    model="my-gpt4o-deployment",
    provider="azure",
    client=project.get_openai_client(),   # a plain openai.OpenAI on /openai/v1/
)
result = run(agent, "Draft a one-line release note.")
```

<!-- tab: TypeScript -->

```ts
import { AIProjectClient } from '@azure/ai-projects';
import { DefaultAzureCredential } from '@azure/identity';
import { Agent, run } from '@cendor/sdk';

const project = new AIProjectClient(
  'https://my-resource.services.ai.azure.com/api/projects/my-project',
  new DefaultAzureCredential(),
);
const agent = new Agent({
  name: 'foundry',
  model: 'my-gpt4o-deployment',
  provider: 'azure',
  client: project.getOpenAIClient(),     // a plain OpenAI client on /openai/v1
});
const result = await run(agent, 'Draft a one-line release note.');
```

<!-- /tabs -->

`azure-ai-projects` / `@azure/ai-projects` is **your** dependency, never Cendor's — the SDK has no
opinion about how the client was built. One cross-language difference worth knowing: the Python
package documents an `api_key=` override on `get_openai_client(...)`, while the JS package always
overwrites `apiKey` with its Entra token provider, so on the JS side authentication goes through
the credential you pass to the constructor.

You can also use the Foundry SDK on the **libraries** door with no agent loop at all — see
[Providers → Azure AI Foundry](/docs/providers#azure-ai-foundry-models-via-the-openai-sdk).

For an OpenAI-family deployment (`gpt-*`, `o*`) you can drive the **Responses API** instead with
`provider="azure_responses"` — same construction, Responses semantics. Keep `provider="azure"`
(Chat Completions) for the broadest reach across non-OpenAI Foundry models.

## Foundry Local

Microsoft **Foundry Local** runs a model on the device and exposes an OpenAI-compatible REST
server — the local counterpart to Ollama. `provider="foundry_local"` points the OpenAI Chat
provider at that endpoint; no key needed. Install with `pip install "cendor-sdk[foundry-local]"` (Python); in
TypeScript it uses the same `openai` peer as the OpenAI provider.

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import Agent, run
from foundry_local import FoundryLocalManager

# FoundryLocalManager starts the service, downloads/loads the model, exposes the endpoint.
mgr = FoundryLocalManager("qwen2.5-0.5b")            # a catalog alias
agent = Agent(
    name="local",
    model=mgr.get_model_info("qwen2.5-0.5b").id,     # the resolved model id, not the alias
    provider="foundry_local",
    base_url=mgr.endpoint,                            # or set FOUNDRY_LOCAL_ENDPOINT
    api_key=mgr.api_key,                              # not required locally; defaults to "none"
)
result = run(agent, "Give me one tip for faster cold starts.")
```

<!-- tab: TypeScript -->

```ts
import { Agent, run } from '@cendor/sdk';

// Foundry Local exposes an OpenAI-compatible endpoint; point the provider at it (no key needed).
// Start the service and resolve the model id with Microsoft's Foundry Local tooling, then:
const agent = new Agent({
  name: 'local',
  model: 'qwen2.5-0.5b-instruct-generic-cpu',   // the resolved model id, not the catalog alias
  provider: 'foundry_local',
  baseURL: 'http://localhost:5273',             // or set FOUNDRY_LOCAL_ENDPOINT
});
const result = await run(agent, 'Give me one tip for faster cold starts.');
```

<!-- /tabs -->

Already running the service yourself? Just pass `base_url=` (or `FOUNDRY_LOCAL_ENDPOINT`) and
skip the manager.

## Gemini, Bedrock & Ollama

These three are prefix-inferable (`gemini-*` → Google, dotted `provider.model` ids → Bedrock,
everything else → Ollama when `provider="ollama"`), so a plain model id is usually enough. Auth
follows the [resolution order above](#api-keys--credentials); the per-provider specifics:

- **Google Gemini** — `GOOGLE_API_KEY` (or `api_key=` / `apiKey`). `run.aio` uses google-genai's
  native async surface (`aio.models.generate_content`) — real concurrency.
- **AWS Bedrock** — **no API key.** Authentication is the standard AWS credential chain
  (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`, an `AWS_PROFILE`, or an
  IAM role) plus a region (`AWS_REGION` / `AWS_DEFAULT_REGION`). Bedrock ids are dotted
  (`anthropic.claude-…`), so keep `provider="bedrock"` explicit. boto3 has no async client, so since
  cendor-sdk 1.14 `run.aio` offloads the blocking `converse` to a thread (`asyncio.to_thread`) — the
  loop stays free; the run's governance scope still attaches. Passing `api_key=` raises a clear error
  (Bedrock has no key); `base_url=` maps to the boto3 / AWS SDK `endpoint_url` / `endpoint`.
- **Ollama** — local, no key. A remote daemon is reached via `OLLAMA_HOST` (the ollama SDK's own
  env var) or `base_url=` / `baseURL` (mapped to the client's `host`). Passing `api_key=` raises a
  clear error — Ollama is local and needs none.

## Provider-author reference

Writing a custom provider adapter (or reading a normalized reply directly)? The parse layer emits
two canonical shapes, and TypeScript exposes the provider seam helpers:

| Symbol | What it is |
|---|---|
| `ParsedResponse` | the normalized model reply — `content`, `tool_calls` (a list of `ToolInvocation`), `finish_reason`, `raw` |
| `ToolInvocation` | one model→SDK tool request — `id`, `name`, `arguments` (a dict) |
| `resolveProvider` / `inferProvider` / `getProvider` *(TS)* | resolve or infer the provider for a model id, or fetch a registered provider |
| `assistantMessage` / `toolResultMessage` *(TS)* | build canonical assistant / tool-result messages |
| `Provider` *(TS type)* | the seam interface a new provider adapter implements |

`ParsedResponse` and `ToolInvocation` are `cendor.sdk` exports in Python and type exports in
TypeScript; the seam helpers are TypeScript-only (Python provider adapters subclass the provider base
in `cendor.sdk.providers`). This is deep surface — most apps never touch it; the loop normalizes for
you.

## Plugs into the stack

Every provider path terminates in a client wrapped by [`cendor-core`](/docs/core)'s `instrument()`
seam, so [budgets, audit, redaction](governance.md) and
[cassette record/replay](governance.md#testing--record-once-replay-forever)
apply identically across all ten — the provider detection, token counting, and pricing are all
core's. Provider-level details that aren't SDK-specific — token counting, price sources, OTel
ingestion — live in the library docs: [Providers & Integration](/docs/providers).

## Honest limits

- **Ten paths, one shape — but capability varies.** Native structured output, token-level
  streaming, and prompt caching each cover a subset (called out on
  [Agents & the loop](agents.md)); everywhere else degrades gracefully.
- **HF/Azure ids need explicit `provider=`** — there is no reliable inference for arbitrary
  Hub/deployment names, and guessing wrong would mis-attribute cost.
- **Deployment-name models start unpriced** — register a rate or budgets can't bind
  ([above](#pricing-unpriced-models)).
- **Async is now uniform.** Gemini uses its native async surface and Bedrock's blocking `converse`
  is offloaded to a thread (since 1.14), so `run.aio` no longer blocks the loop for any provider.
  Usage capture itself is complete: all ten paths capture cost and tokens end-to-end in **both**
  languages, via `@cendor/core`'s provider detection ([parity matrix](/docs/languages)).
