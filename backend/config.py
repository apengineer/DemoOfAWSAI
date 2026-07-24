"""Configuration loading for the eKYC onboarding demo.

Loads YAML config from the ``config/`` directory and applies environment
variable overrides. Kept deliberately simple and dependency-light so the
same module works locally and inside an AgentCore Runtime container.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def _load_yaml(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / name
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def app_config() -> Dict[str, Any]:
    cfg = _load_yaml("app.yaml")

    # ---- Environment overrides (env wins over file) ----
    aws = cfg.setdefault("aws", {})
    aws["region"] = os.getenv("AWS_REGION", aws.get("region", "us-east-1"))
    aws["model_id"] = os.getenv("BEDROCK_MODEL_ID", aws.get("model_id"))

    mem = cfg.setdefault("memory", {})
    mem["backend"] = os.getenv("MEMORY_BACKEND", mem.get("backend", "auto"))
    mem["memory_id"] = os.getenv("AGENTCORE_MEMORY_ID", mem.get("memory_id", "")) or ""

    agent = cfg.setdefault("agent", {})
    # mode: "local" runs the Strands agent in-process; "runtime" invokes the
    # deployed AgentCore Runtime via its ARN.
    agent["mode"] = os.getenv("AGENT_MODE", agent.get("mode", "local"))
    agent["runtime_arn"] = os.getenv("AGENTCORE_RUNTIME_ARN", agent.get("runtime_arn", "")) or ""

    server = cfg.setdefault("server", {})
    server["host"] = os.getenv("HOST", server.get("host", "127.0.0.1"))
    server["port"] = int(os.getenv("PORT", server.get("port", 8080)))

    return cfg


@lru_cache(maxsize=1)
def flow_config() -> Dict[str, Any]:
    return _load_yaml("flow.yaml")


@lru_cache(maxsize=1)
def tenants_config() -> Dict[str, Any]:
    return _load_yaml("tenants.yaml")


def resolve_memory_backend() -> str:
    """Decide which memory backend to use given config + environment."""
    mem = app_config()["memory"]
    backend = (mem.get("backend") or "auto").lower()
    if backend == "auto":
        # Use AgentCore Memory only if a memory id was provided.
        return "agentcore" if mem.get("memory_id") else "local"
    return backend
