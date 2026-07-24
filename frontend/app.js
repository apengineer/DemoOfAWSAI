// Minimal vanilla-JS frontend for the eKYC onboarding demo.
const $ = (id) => document.getElementById(id);
let CONFIG = null;
let SESSION = null;
let STEPS = [];

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`${res.status}: ${t}`);
  }
  return res.json();
}

function addMsg(text, role) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  $("chat").appendChild(div);
  $("chat").scrollTop = $("chat").scrollHeight;
}

function populateUsers() {
  const tenant = CONFIG.tenants.find((t) => t.id === $("tenant").value);
  $("user").innerHTML = "";
  tenant.users.forEach((u) => {
    const o = document.createElement("option");
    o.value = u.id;
    o.textContent = u.name;
    $("user").appendChild(o);
  });
}

function renderSteps(state) {
  const done = new Set((state && state.completed_steps) || []);
  const results = (state && state.step_results) || {};
  $("steps").innerHTML = "";
  STEPS.forEach((s) => {
    const li = document.createElement("li");
    const failed = results[s.id] && results[s.id].status === "failed";
    li.className = done.has(s.id) ? (failed ? "failed" : "done") : "";
    li.innerHTML = `<span class="dot"></span><span class="name">${s.title}</span><span class="tag">${s.id}</span>`;
    $("steps").appendChild(li);
  });
  const dec = $("decision");
  dec.className = "decision";
  if (state && state.decision === "approve") { dec.classList.add("approve"); dec.textContent = "DECISION: APPROVED"; }
  else if (state && (state.decision === "refer")) { dec.classList.add("refer"); dec.textContent = "DECISION: REFER (manual review)"; }
  else { dec.textContent = ""; }
}

function renderTrace(trace) {
  const box = $("trace");
  box.innerHTML = "";
  if (!trace) { box.innerHTML = '<p class="sub">No trace yet.</p>'; return; }
  const total = trace.total_ms || 1;
  const head = document.createElement("div");
  head.className = "trace-total";
  head.textContent = `trace ${trace.trace_id} · ${trace.span_count} spans · ${trace.total_ms} ms`;
  box.appendChild(head);
  trace.spans.forEach((s) => {
    const row = document.createElement("div");
    row.className = "span-row" + (s.status === "ERROR" ? " err" : "");
    const w = Math.max(2, Math.round((s.duration_ms / total) * 160));
    row.innerHTML = `<span class="lbl">${s.name}</span><span class="bar" style="width:${w}px"></span><span class="ms">${s.duration_ms}ms</span>`;
    box.appendChild(row);
  });
}

async function refreshTrace() {
  if (!SESSION) return;
  try {
    const t = await api(`/api/trace?session_id=${encodeURIComponent(SESSION.session_id)}`);
    renderTrace(t.latest);
  } catch (e) { /* ignore */ }
}

async function startSession(forceNew) {
  const tenant_id = $("tenant").value;
  const user_id = $("user").value;
  SESSION = await api("/api/session/start", {
    method: "POST",
    body: JSON.stringify({ tenant_id, user_id, force_new: !!forceNew }),
  });
  $("actor-id").textContent = SESSION.actor_id;
  $("session-info").textContent =
    `session=${SESSION.session_id} · microVM=${SESSION.microvm} · ${SESSION.resumed ? "RESUMED" : "NEW"}`;
  $("chat").innerHTML = "";
  renderSteps(SESSION.state);

  if (SESSION.resumed) {
    const p = await api(`/api/progress?tenant_id=${tenant_id}&user_id=${user_id}&session_id=${encodeURIComponent(SESSION.session_id)}`);
    (p.history || []).forEach((m) => addMsg(m.content, m.role));
    addMsg(`Resumed onboarding from AgentCore Memory (${(SESSION.state.completed_steps||[]).length} steps already complete).`, "system");
  } else {
    addMsg("New onboarding session started. Say hello to begin.", "system");
  }
  await refreshTrace();
}

async function sendMessage(e) {
  e.preventDefault();
  if (!SESSION) { alert("Start a session first."); return; }
  const text = $("msg").value.trim();
  if (!text) return;
  $("msg").value = "";
  addMsg(text, "user");
  $("send-btn").disabled = true;
  try {
    const r = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        tenant_id: SESSION.tenant_id, user_id: SESSION.user_id,
        session_id: SESSION.session_id, message: text,
      }),
    });
    addMsg(r.reply, "assistant");
    SESSION.state = r.state;
    renderSteps(r.state);
    renderTrace(r.trace);
  } catch (err) {
    addMsg("Error: " + err.message, "system");
  } finally {
    $("send-btn").disabled = false;
    $("msg").focus();
  }
}

async function isolationTest() {
  if (!SESSION) { alert("Start a session first."); return; }
  // Pick a different tenant/user as the "target".
  let target = null;
  for (const t of CONFIG.tenants) {
    if (t.id !== SESSION.tenant_id) { target = { tenant: t.id, user: t.users[0].id }; break; }
  }
  if (!target) { $("isolation-out").textContent = "Need a second tenant to demonstrate isolation."; return; }
  const out = await api("/api/isolation/cross-tenant-test", {
    method: "POST",
    body: JSON.stringify({
      requesting_tenant: SESSION.tenant_id, requesting_user: SESSION.user_id,
      target_tenant: target.tenant, target_user: target.user,
    }),
  });
  const verdict = out.isolated
    ? `ISOLATED ✓  tenant '${SESSION.tenant_id}' read 0 records from '${target.tenant}'`
    : `LEAK ✗  ${out.records_returned} records returned`;
  $("isolation-out").innerHTML =
    `<span class="${out.isolated ? "isolated-ok" : "isolated-bad"}">${verdict}</span>\n\n` +
    JSON.stringify(out, null, 2);
}

async function init() {
  CONFIG = await api("/api/config");
  STEPS = CONFIG.flow.steps;
  $("product-name").textContent = CONFIG.branding.product_name || "Identity Onboarding Studio";
  $("tagline").textContent = CONFIG.branding.tagline || "";
  if (CONFIG.branding.logo_text) $("logo").textContent = CONFIG.branding.logo_text;
  if (CONFIG.branding.primary_color) {
    document.documentElement.style.setProperty("--primary", CONFIG.branding.primary_color);
  }
  $("model-badge").textContent = "model: " + CONFIG.model_id;
  $("memory-badge").textContent = "memory: " + (CONFIG.memory.backend || "?");
  $("region-badge").textContent = "region: " + CONFIG.region;
  const am = $("agent-badge");
  am.textContent = "agent: " + (CONFIG.agent_mode || "local");
  // Highlight runtime mode so the audience sees we're hitting AgentCore Runtime.
  if (CONFIG.agent_mode === "runtime") am.style.cssText = "background:var(--primary);color:#fff;border-color:var(--primary)";

  CONFIG.tenants.forEach((t) => {
    const o = document.createElement("option");
    o.value = t.id; o.textContent = t.name;
    $("tenant").appendChild(o);
  });
  populateUsers();
  renderSteps(null);

  $("tenant").addEventListener("change", populateUsers);
  $("start-btn").addEventListener("click", () => startSession(false));
  $("newattempt-btn").addEventListener("click", () => startSession(true));
  $("chat-form").addEventListener("submit", sendMessage);
  $("isolation-btn").addEventListener("click", isolationTest);
}

init().catch((e) => alert("Init failed: " + e.message));
