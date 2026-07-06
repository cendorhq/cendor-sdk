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

Normalization is isolated in `providers.py` with per-provider fixtures, so response-shape drift
is contained. In TypeScript, OpenAI (Chat + Responses) and Anthropic ship first, with the same
`Provider` seam for the rest — see the [parity matrix](/docs/languages).

## Core concepts

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
budgets work:

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

Or use a `tokens=` cap (tokens are counted regardless of price), or
`configure(on_unpriced="raise")` to reject unpriced calls outright.

## Hugging Face

> **Now in TypeScript.** The Hugging Face, Azure AI Foundry, and Foundry Local providers below are
> ported to `@cendor/sdk`. Azure and Foundry Local wrap the standard `openai` client, so cost and
> usage are captured end-to-end today; Hugging Face parses responses now, with usage capture
> arriving alongside the matching `@cendor/core` detection. See the [parity matrix](/docs/languages).

Uses `huggingface_hub.InferenceClient.chat_completion` — the response is OpenAI-shaped, and the
call is attributed to `huggingface`, not `openai`. The `model` is a Hub id or an Inference
Endpoint URL. Install with `pip install "cendor-sdk[huggingface]"`.

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
construction. Install with `pip install "cendor-sdk[azure]"` (it just pulls `openai`). Two rules:

1. **`model` is your deployment name**, not the underlying model name (Azure keys on deployment).
2. **`base_url` is the Foundry endpoint** — `/openai/v1/` is appended for you; also read from
   `AZURE_OPENAI_ENDPOINT`. No `api-version` needed (the v1 GA API infers it).

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
    api_key="<resource-key>",          # or AZURE_OPENAI_API_KEY / AZURE_INFERENCE_CREDENTIAL
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
  apiKey: '<resource-key>',           // or AZURE_OPENAI_API_KEY / AZURE_INFERENCE_CREDENTIAL
});

const result = await withBudget({ usd: 0.10, onExceed: 'block' }, () =>
  run(agent, 'Draft a one-line release note.'));
```

<!-- /tabs -->

**Microsoft Entra ID (keyless):** pass a bearer-token provider as `api_key` (the v1 client
refreshes it), or build the client yourself and hand it to `Agent(client=...)`:

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

> **Python only (for now).** The Azure provider itself is in `@cendor/sdk` (above), but keyless
> **Entra ID token-provider** auth — passing a *refreshing* bearer-token callback as `api_key` — is
> Python-only. The TS Azure provider takes a string `apiKey` or a pre-built `client`, so hand it a
> client you refresh yourself. See the [parity matrix](/docs/languages).

<!-- /tabs -->

For an OpenAI-family deployment (`gpt-*`, `o*`) you can drive the **Responses API** instead with
`provider="azure_responses"` — same construction, Responses semantics. Keep `provider="azure"`
(Chat Completions) for the broadest reach across non-OpenAI Foundry models.

## Foundry Local

Microsoft **Foundry Local** runs a model on the device and exposes an OpenAI-compatible REST
server — the local counterpart to Ollama. `provider="foundry_local"` points the OpenAI Chat
provider at that endpoint; no key needed. Install with `pip install "cendor-sdk[foundry-local]"`.

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

## Plugs into the stack

Every provider path terminates in an instrumented client, so
[budgets, audit, redaction](governance.md) and [cassette record/replay](governance.md#testing--record-once-replay-forever)
apply identically across all ten. Provider-level details that aren't SDK-specific — token
counting, price sources, OTel ingestion — live in the library docs:
[Providers & Integration](/docs/providers).

## Honest limits

- **Ten paths, one shape — but capability varies.** Native structured output, token-level
  streaming, and prompt caching each cover a subset (called out on
  [Agents & the loop](agents.md)); everywhere else degrades gracefully.
- **HF/Azure ids need explicit `provider=`** — there is no reliable inference for arbitrary
  Hub/deployment names, and guessing wrong would mis-attribute cost.
- **Deployment-name models start unpriced** — register a rate or budgets can't bind
  ([above](#pricing-unpriced-models)).
- **TypeScript ships OpenAI + Anthropic first.** The seam is identical; the remaining providers
  land per the [parity matrix](/docs/languages).
