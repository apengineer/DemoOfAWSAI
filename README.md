# Agentic eKYC Assistant — Amazon Bedrock AgentCore Demo

A reusable, vendor-neutral demo of an agentic eKYC (identity onboarding) assistant.
It showcases exactly three Amazon Bedrock AgentCore capabilities — Runtime (session
isolation), Memory (per-tenant/user isolation plus pause/resume), and Observability
(per-turn traces) — on top of Amazon Bedrock as the foundation model.

No customer name is baked in. Branding, tenants, and the onboarding flow are all
config-driven, so the same code re-skins for any customer.

## What it demonstrates

- Runtime isolation: each (tenant, user, attempt) maps to a distinct runtime session,
  which becomes a dedicated isolated microVM when deployed.
- Memory isolation and continuity: every memory namespace is scoped by tenant and user,
  and onboarding can pause and resume because all durable state lives in AgentCore Memory.
- Observability: per-turn traces of the agent's reasoning, tool calls, and memory access.

## Model note

The user originally requested Claude 3.5 Sonnet, but that model is legacy/unavailable on
this AWS account. The demo defaults to Claude Sonnet 4.5
(`us.anthropic.claude-sonnet-4-5-20250929-v1:0`). The model is configurable via
`config/app.yaml` or the `BEDROCK_MODEL_ID` environment variable.

## Architecture

The FastAPI backend (`backend/app.py`) is stateless. All durable state lives in
AgentCore Memory, which is what makes the isolation story crisp. On each turn the backend:

1. Loads conversation history and onboarding state from Memory.
2. Builds a fresh Strands Agent (Claude on Bedrock) with config-driven mock verification tools.
3. Runs the agent.
4. Persists the new turn and updated state back to Memory.

Identifiers:

- `actorId` = `"<tenant_id>__<user_id>"` (double underscore; the AgentCore actorId regex
  forbids `#`).
- `sessionId` = the onboarding attempt id, also used as the `runtimeSessionId` when deployed.
- Long-term namespaces are tenant + user scoped:
  - `/ekyc/summaries/{actorId}/{sessionId}/`
  - `/ekyc/preferences/{actorId}/`
  - `/ekyc/facts/{actorId}/`

Runtime isolation: the backend owns the user-to-session mapping (AgentCore deliberately
does not). Each (tenant, user, attempt) maps to a distinct `runtimeSessionId`, which maps
to a dedicated isolated microVM when deployed. Locally, a representative microVM id is
shown in the UI.

Memory isolation: because every namespace is scoped by `actorId` (which encodes the
tenant), one tenant cannot read another's memory. This can be hard-enforced with IAM
namespace/namespacePath conditions (see `deploy/iam_execution_role.json`).

Observability: an in-app span recorder powers the UI "Observability" panel locally. Spans
include `agent.session`, `memory.load_state`, `memory.load_history`, `agent.reason`,
`tool.*`, `memory.create_event`, and `memory.save_state`. When deployed to AgentCore
Runtime, OpenTelemetry is auto-instrumented and traces/metrics appear in CloudWatch GenAI
observability (this requires enabling CloudWatch Transaction Search once per account).

## Project structure

- `config/app.yaml` — region, model, memory backend and id, namespaces, observability, server.
- `config/flow.yaml` — eKYC steps, mock outcomes, and fail keywords.
- `config/tenants.yaml` — branding plus tenants/users (all fictional).
- `backend/`
  - `config.py`
  - `memory_store.py` — `AgentCoreMemoryStore` and `LocalMemoryStore`.
  - `tools.py` — mock verification tools and `SessionContext`.
  - `agent.py` — `EkycAgentService`.
  - `session_manager.py`
  - `observability.py`
  - `app.py`
- `frontend/` — `index.html`, `styles.css`, `app.js` (light vanilla JS single page).
- `scripts/` — `provision_memory.py`, `smoke_test.py`.
- `deploy/`
  - `agent_runtime.py` — `BedrockAgentCoreApp` entrypoint.
  - `Dockerfile` — ARM64.
  - `deploy_runtime.py`
  - `requirements.txt`
  - `iam_execution_role.json`
- `requirements.txt`, `.env.example`, `.env`.

## Setup

A Python 3.11 virtual environment lives at `.venv`, created with `uv` (the system python
is 3.9 and too old). Activate it:

```
source .venv/bin/activate
```

Dependencies are in `requirements.txt`: strands-agents 1.44.0, strands-agents-tools,
bedrock-agentcore 1.14.1, boto3, fastapi, uvicorn, pydantic, pyyaml, python-dotenv.
Install them with:

```
uv pip install -r requirements.txt
```

(or `pip install -r requirements.txt`).

AWS region is `us-east-1`. The machine's `default` AWS profile is expired; use the working
profile `agentDemoUser`. The provided `.env` sets `AWS_PROFILE=agentDemoUser`. Credentials
must have Bedrock model access plus AgentCore permissions.

Memory: an AgentCore Memory resource is already provisioned with id
`EkycOnboardingMemory-IEyPJCDrEr` (set in `config/app.yaml` and `.env` as
`AGENTCORE_MEMORY_ID`). To create a fresh one, run `python scripts/provision_memory.py` and
put the printed id into `config/app.yaml` (`memory.memory_id`) or env `AGENTCORE_MEMORY_ID`.

Memory backend selection (`config/app.yaml` `memory.backend`):

- `auto` — agentcore if a memory_id is set, otherwise local file fallback.
- `agentcore` — always use AgentCore Memory.
- `local` — store files under `.memory_store/`; needs no AWS Memory resource (good offline fallback).

## Run locally

1. `source .venv/bin/activate`
2. Ensure `.env` exists (copy from `.env.example`; it sets `AWS_PROFILE=agentDemoUser`,
   `AWS_REGION=us-east-1`, `BEDROCK_MODEL_ID`, `MEMORY_BACKEND=auto`, `AGENTCORE_MEMORY_ID`).
3. Start the server:
   ```
   AWS_PROFILE=agentDemoUser uvicorn backend.app:app --host 127.0.0.1 --port 8080
   ```
4. Open http://127.0.0.1:8080
5. Optional API smoke test (server must be running): `python scripts/smoke_test.py`

## Live demo script

This is the centerpiece. Each step maps to the AgentCore capability it shows.

1. Start a session (Runtime + Memory + Observability).
   Pick tenant NorthBank, user Alice, and click Start. Chat:
   "Hi, my name is Alice Nguyen, born 1990-05-12, Ireland." Then: "I'll use my passport."
   Watch the progress steps complete (Memory) and the Observability panel show spans per
   turn (Observability). Mention that each chat turn is a Bedrock model call and that tool
   calls advance the flow.

2. Pause and resume (Memory).
   Reload the page (or click Start again for the same tenant/user). Point out that it
   resumes from AgentCore Memory with prior steps already complete and the conversation
   restored — even though the backend is stateless. This is the long/short-term Memory story.

3. Retry after a failure (tools + Observability).
   Type a message containing the keyword "blurry" (document), "spoof" (liveness), or
   "mismatch" (face) to force that step to fail, then retry. This shows tool-driven
   verification and observability of a failed span.

4. Tenant isolation (Memory isolation).
   Click "Try cross-tenant memory read." NorthBank/Alice's session attempts to read
   SouthTrust/Carla's memory and gets 0 records (isolated). Explain `actorId` scoping and
   that IAM can hard-enforce it. This is the SaaS multi-tenant story, complementing Runtime
   per-session microVM isolation.

5. Reusability.
   Explain that editing `config/flow.yaml` (steps) and `config/tenants.yaml`
   (branding/tenants) re-skins the demo for any customer with no code changes.

## HTTP API

- `GET /api/config`
- `POST /api/session/start` — `{tenant_id, user_id, force_new}`
- `POST /api/chat` — `{tenant_id, user_id, session_id, message}`
- `GET /api/progress?tenant_id&user_id&session_id`
- `GET /api/trace?session_id`
- `POST /api/isolation/cross-tenant-test` — `{requesting_tenant, requesting_user, target_tenant, target_user}`
- `GET /api/health`

## Deploy to AgentCore Runtime

The container reuses the same agent code as the local demo. Pass the same value as
`session_id` and `runtimeSessionId` so Runtime isolation and Memory scoping align.

1. Enable CloudWatch Transaction Search once per account (console: CloudWatch > Settings >
   Transaction Search, or via API) for observability.
2. Create the execution role from `deploy/iam_execution_role.json` (fill in
   `ACCOUNT_ID`/`REGION`/`MEMORY_ID`).
3. Print the exact build steps:
   ```
   python deploy/deploy_runtime.py --print-build-steps
   ```
   This prints the ECR create/login/build/push commands (ARM64 via
   `docker buildx build --platform linux/arm64 -f deploy/Dockerfile ... --push .`).
4. Deploy:
   ```
   python deploy/deploy_runtime.py --container-uri <ecr-uri> --role-arn <role-arn>
   ```
   This creates the runtime (HTTP protocol, PUBLIC network) plus a `live` endpoint and waits
   until READY.

Note: PUBLIC network with no authorizer is for demo only. Production should use VPC mode
and an authorizer.

## Troubleshooting

- `UnrecognizedClientException` / `InvalidClientTokenId`: the default AWS profile is expired;
  use `AWS_PROFILE=agentDemoUser`.
- `ThrottlingException`: `maxTokens` is set explicitly (1024) in config; lower
  temperature/tokens or check Bedrock quotas if needed.
- Model access errors: enable model access for the chosen Bedrock model in the
  account/region, or change `BEDROCK_MODEL_ID`.
- To run without any AWS Memory resource: set `MEMORY_BACKEND=local` (uses `.memory_store/`).
