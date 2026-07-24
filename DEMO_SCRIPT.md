# Demo Script — Agentic eKYC Onboarding on Amazon Bedrock AgentCore

A presenter-ready walkthrough. The whole thing runs in about 12–15 minutes.

**The one story to tell:** the agent itself is stateless and disposable —
AgentCore gives it secure compute (Runtime), durable per-tenant memory (Memory),
and full traceability (Observability), so a SaaS identity vendor can run many
tenants safely.

**Two-sentence intro:**
This is an agentic eKYC onboarding assistant that guides a customer through
identity verification — collecting details, checking their ID document, liveness,
and face match — using Amazon Bedrock for the reasoning and Amazon Bedrock
AgentCore for Runtime, Memory, and Observability. It's built to show how a SaaS
identity platform can run many tenants safely: each session runs in isolated
compute, every customer's progress is remembered and kept separate in managed
memory, and every agent decision is fully traceable.

---

## 0. Before you start (off-camera, ~2 min)

- Start the server:
  ```
  source .venv/bin/activate
  AWS_PROFILE=agentDemoUser uvicorn backend.app:app --host 127.0.0.1 --port 8080
  ```
- Open these browser tabs, pre-logged-in:
  1. The app — http://127.0.0.1:8080
  2. Bedrock console — https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/overview
  3. AgentCore in console — https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/agentcore
  4. CloudWatch GenAI Observability — https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#gen-ai-observability:
- Have a terminal ready for the optional "live proof" CLI commands.
- Sticky-note facts: region **us-east-1**, model **Claude Sonnet 4.5**,
  memory id **EkycOnboardingMemory-IEyPJCDrEr**.
- Pre-seed a second tenant: run a couple of turns as SouthTrust / Carla so the
  isolation comparison has data on both sides.

---

## 1. Frame the problem (~1 min, no clicks)

Say: "A digital identity platform runs identity journeys for many institutions.
If you put an AI agent in that flow, three things matter immediately: where does
the agent run, where does its memory live, and can you prove what it did. Amazon
Bedrock provides the model; Bedrock AgentCore provides Runtime, Memory, and
Observability for exactly those three. Let me show a working eKYC onboarding
agent."

---

## 2. App demo — the happy path (~3 min)

Point at the header badges first: "model = Claude Sonnet 4.5 on Bedrock,
memory = agentcore, region = us-east-1. This is talking to real AWS, not mocks."

1. Select **NorthBank / Alice Nguyen**, click **Start**. Note the session line:
   `session=… · microVM=… · NEW`.
   - Say: "That microVM id represents Runtime's per-session isolation — each
     onboarding attempt gets its own isolated compute."
2. Type: `Hi, I'm Alice Nguyen, born 1990-05-12, Ireland.` and Send.
3. Type: `I'll use my passport.` and Send.
   - As steps light up green in the **Onboarding progress** panel, say: "Each
     reply is a Bedrock call. The agent decides which verification tool to invoke
     — document auth, liveness, face match — and the progress you see is written
     to AgentCore Memory, not held in the web server."
4. Glance at the **Observability** panel: "Every turn produces a trace: load
   memory, agent reasoning on Bedrock, the tool calls, write back to memory —
   each with real latency."

---

## 3. App demo — Memory pause/resume (~2 min) — the showstopper

1. **Reload the browser** (or click Start again for NorthBank / Alice).
   - Say: "I just threw away the session. Watch." The chat repopulates, the
     completed steps are still green, and the session shows `RESUMED`.
2. Say: "The backend is completely stateless — I could kill the process. The
   conversation and onboarding progress came back because they live in AgentCore
   Memory, scoped to this tenant and user. Short-term memory holds the transcript;
   long-term memory holds the durable facts. This is the 'come back days later and
   continue' experience."

---

## 4. App demo — retry + tenant isolation (~2 min)

1. **Retry:** type something containing `blurry`, e.g.
   `Here's my passport photo, it's a little blurry.` The document step fails (red)
   and the agent asks for a retake; then `I retook a clear photo.` and it passes.
   - Say: "The model never fakes a result — it reports exactly what the
     verification tool returned, and the failed step shows up in the trace too."
2. **Isolation:** click **Try cross-tenant memory read**. It returns
   `ISOLATED ✓ … 0 records`.
   - Say: "NorthBank's session just tried to read SouthTrust's memory and got
     nothing. Isolation comes from scoping every memory namespace by tenant, and
     it can be hard-enforced with IAM. For a SaaS provider, that's the whole
     ballgame."

---

## 5. Jump to the AWS Console (~4–5 min)

### A. Amazon Bedrock — the model (~1 min)
- Tab 2 (Bedrock overview), left nav **Model access**: show Claude models are
  enabled. Then **Inference profiles** (or Providers, Anthropic) and point to
  **Claude Sonnet 4.5**.
- Say: "This is the foundation model the agent calls via the Bedrock Converse API.
  Swapping models is one config line — no app changes."

### B. AgentCore — Memory (~2 min, the strongest console moment)
- Tab 3 (AgentCore), **Memory**, open **EkycOnboardingMemory-IEyPJCDrEr**.
- Show its **strategies / namespaces**: summary, user-preference, semantic — each
  templated by `actorId` / `sessionId`.
- Say: "This is the managed memory store behind the demo. `actorId = tenant__user`,
  so every record is tenant-scoped by construction."
- **Live proof (reliable — do this in the terminal):**
  ```
  AWS_PROFILE=agentDemoUser aws bedrock-agentcore list-sessions \
    --memory-id EkycOnboardingMemory-IEyPJCDrEr \
    --actor-id northbank__alice --region us-east-1
  ```
  Say: "Those are Alice's real onboarding sessions. Run it for `southtrust__carla`
  and you get a completely separate set — that's the isolation, live."

### C. AgentCore — Observability / CloudWatch (~1–2 min)
- Tab 4 (CloudWatch GenAI Observability).
- Say honestly: "Locally I'm showing traces in the app's own panel. The moment
  this agent is deployed to AgentCore Runtime, OpenTelemetry is auto-instrumented
  and these same spans — model latency, tool calls, token usage — land here in
  CloudWatch GenAI observability, with per-session traces and dashboards."
  (Requires enabling CloudWatch Transaction Search once per account.)
- If you've already deployed: open the agent here and show a live trace plus the
  **AgentCore, Runtime** page with the runtime and endpoint.

### D. AgentCore — Runtime (~30 sec)
- AgentCore, **Runtime / Agents**. If deployed, show the runtime and endpoint and
  say "each invocation runs in an isolated microVM, scales to zero, framework-
  agnostic." If not deployed yet: "Deployment is a container build plus one
  create-runtime call — artifacts are in the repo's `deploy/` folder."

---

## 6. Close (~1 min)

"To recap against the three asks: **Runtime** gives isolated, serverless compute
per session; **Memory** gives durable, per-tenant/user state that survives
restarts and enforces isolation; **Observability** gives full traceability of
every agent decision. The eKYC use case is just config — `flow.yaml` and
`tenants.yaml` re-skin this for any journey or customer with no code changes. And
it's all on Amazon Bedrock with the model of your choice."

---

## Tips / safety nets

- **If a Bedrock call is slow or throttles:** keep talking through the
  Observability panel; responses take ~5–6s, that's normal.
- **If the console Memory UI looks thin:** the CLI `list-sessions` / `list-events`
  commands are your dependable live proof.
- **Don't promise Claude 3.5 Sonnet** — say "Claude Sonnet on Bedrock"; the
  account runs Sonnet 4.5.
- **Pre-seed both tenants** before the demo so the isolation comparison has data
  on both sides.

### Forced-failure keywords (for the retry moment)
- `blurry` — fails document verification
- `spoof` — fails the liveness check
- `mismatch` — fails the face match
