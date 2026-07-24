"""Provision (or reuse) the AgentCore Memory resource for this demo.

Creates one Memory resource with three long-term strategies (summary,
user-preference, semantic), each scoped by a tenant+user actorId namespace.
Run once, then put the printed memory id into config/app.yaml (memory.memory_id)
or the AGENTCORE_MEMORY_ID environment variable to switch the demo onto the
real AgentCore Memory backend.

Usage:
    python scripts/provision_memory.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402

from backend.config import app_config  # noqa: E402


def main() -> None:
    cfg = app_config()
    region = cfg["aws"]["region"]
    mem = cfg["memory"]
    name = mem.get("memory_name", "EkycOnboardingMemory")
    ns = mem.get("namespaces", {})

    control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Looking for an existing memory named '{name}' in {region} ...")
    existing = None
    token = None
    while True:
        kwargs = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        resp = control.list_memories(**kwargs)
        for m in resp.get("memories", []):
            if m.get("id", "").startswith(name):
                existing = m
                break
        token = resp.get("nextToken")
        if existing or not token:
            break

    if existing:
        print(f"Reusing existing memory: {existing['id']} (status={existing.get('status')})")
        memory_id = existing["id"]
    else:
        print("Creating a new AgentCore Memory resource with 3 long-term strategies ...")
        resp = control.create_memory(
            name=name,
            description="eKYC onboarding demo - multi-tenant (actorId = tenant#user)",
            eventExpiryDuration=int(mem.get("event_expiry_days", 90)),
            memoryStrategies=[
                {"summaryMemoryStrategy": {
                    "name": "SessionSummarizer",
                    "namespaceTemplates": [ns.get("summary", "/ekyc/summaries/{actorId}/{sessionId}/")],
                }},
                {"userPreferenceMemoryStrategy": {
                    "name": "PreferenceLearner",
                    "namespaceTemplates": [ns.get("preferences", "/ekyc/preferences/{actorId}/")],
                }},
                {"semanticMemoryStrategy": {
                    "name": "FactExtractor",
                    "namespaceTemplates": [ns.get("semantic", "/ekyc/facts/{actorId}/")],
                }},
            ],
        )
        memory_id = resp["memory"]["id"]
        print(f"Created memory: {memory_id}")

    print("Waiting for memory to become ACTIVE ...")
    while True:
        status = control.get_memory(memoryId=memory_id)["memory"].get("status")
        if status == "ACTIVE":
            break
        if status == "FAILED":
            raise SystemExit("Memory creation FAILED")
        time.sleep(10)

    print("\n=== DONE ===")
    print(f"Memory ID: {memory_id}")
    print("Enable the AgentCore backend by setting either:")
    print(f"  - config/app.yaml  -> memory.memory_id: {memory_id}")
    print(f"  - or environment   -> export AGENTCORE_MEMORY_ID={memory_id}")


if __name__ == "__main__":
    main()
