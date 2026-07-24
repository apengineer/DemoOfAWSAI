import json
import urllib.request

BASE = "http://127.0.0.1:8080"


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


print("=== 1) START NEW (northbank/alice) ===")
s = post("/api/session/start", {"tenant_id": "northbank", "user_id": "alice", "force_new": True})
sid = s["session_id"]
print("session:", sid, "| actor:", s["actor_id"], "| microvm:", s["microvm"], "| resumed:", s["resumed"])

print("\n=== 2) CHAT turn 1 (details) ===")
r = post("/api/chat", {"tenant_id": "northbank", "user_id": "alice", "session_id": sid,
                       "message": "Hi, my name is Alice Nguyen, born 1990-05-12, country Ireland."})
print("reply:", r["reply"][:160])
print("completed:", r["state"].get("completed_steps"))

print("\n=== 3) CHAT turn 2 (passport) ===")
r = post("/api/chat", {"tenant_id": "northbank", "user_id": "alice", "session_id": sid,
                       "message": "I'll use my passport."})
print("reply:", r["reply"][:160])
print("completed:", r["state"].get("completed_steps"))
print("trace spans:", [(sp["name"], sp["duration_ms"]) for sp in (r["trace"]["spans"] if r.get("trace") else [])])

print("\n=== 4) RESUME (fresh start picks same session from AgentCore Memory) ===")
s2 = post("/api/session/start", {"tenant_id": "northbank", "user_id": "alice"})
print("resumed:", s2["resumed"], "| session:", s2["session_id"], "| completed:", s2["state"].get("completed_steps"))

print("\n=== 5) PROGRESS history length (from AgentCore short-term memory) ===")
p = get(f"/api/progress?tenant_id=northbank&user_id=alice&session_id={sid}")
print("history turns:", len(p["history"]))

print("\n=== 6) CROSS-TENANT ISOLATION (northbank/alice -> southtrust/carla) ===")
iso = post("/api/isolation/cross-tenant-test", {
    "requesting_tenant": "northbank", "requesting_user": "alice",
    "target_tenant": "southtrust", "target_user": "carla"})
print(json.dumps(iso, indent=2))
