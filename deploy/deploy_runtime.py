"""Deploy the eKYC agent to Amazon Bedrock AgentCore Runtime - no Docker.

Uses the AgentCore starter toolkit's direct code deployment (zip -> S3),
which needs no Dockerfile, no ECR, and no local container build. The toolkit
auto-creates the S3 bucket and the runtime execution role.

After launch this script also ensures the execution role can reach the
AgentCore Memory data plane (CreateEvent/ListEvents/RetrieveMemoryRecords) and
invoke the Bedrock model, since the agent calls both from inside the runtime.

Run from the project root:
    source .venv/bin/activate
    AWS_PROFILE=agentDemoUser python deploy/deploy_runtime.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import boto3  # noqa: E402

from backend.config import app_config  # noqa: E402

AGENT_NAME = "ekyc_onboarding_agent"


def _memory_policy(region: str, account: str, memory_id: str) -> dict:
    mem_arn = f"arn:aws:bedrock-agentcore:{region}:{account}:memory/{memory_id}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AgentCoreMemoryDataPlane",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:ListSessions",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:GetMemory",
                ],
                "Resource": mem_arn,
            },
            {
                "Sid": "InvokeBedrockModels",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/anthropic.*",
                    f"arn:aws:bedrock:{region}:{account}:inference-profile/*",
                ],
            },
        ],
    }


def ensure_role_permissions(region: str, runtime_id: str, memory_id: str) -> None:
    ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
    sts = boto3.client("sts", region_name=region)
    account = sts.get_caller_identity()["Account"]
    try:
        desc = ctrl.get_agent_runtime(agentRuntimeId=runtime_id)
        role_arn = desc.get("roleArn") or desc.get("agentRuntime", {}).get("roleArn")
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not read runtime role: {exc})")
        role_arn = None

    if not role_arn:
        print("  Could not resolve execution role; attach this policy manually:")
        print(json.dumps(_memory_policy(region, account, memory_id), indent=2))
        return

    role_name = role_arn.split("/")[-1]
    iam = boto3.client("iam")
    try:
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="EkycMemoryAndModelAccess",
            PolicyDocument=json.dumps(_memory_policy(region, account, memory_id)),
        )
        print(f"  Attached memory+model policy to role {role_name}.")
    except Exception as exc:  # noqa: BLE001
        print(f"  Could not attach policy automatically ({exc}).")
        print(f"  Attach this inline policy to role {role_name} manually:")
        print(json.dumps(_memory_policy(region, account, memory_id), indent=2))


def main() -> None:
    cfg = app_config()
    region = cfg["aws"]["region"]
    memory_id = cfg["memory"]["memory_id"]
    model_id = cfg["aws"]["model_id"]

    if not memory_id:
        raise SystemExit("memory.memory_id is not set in config/app.yaml")

    from bedrock_agentcore_starter_toolkit import Runtime

    rt = Runtime()

    print("Configuring AgentCore Runtime (direct code deploy, no Docker) ...")
    rt.configure(
        entrypoint=str(PROJECT_ROOT / "runtime_entrypoint.py"),
        agent_name=AGENT_NAME,
        requirements_file=str(PROJECT_ROOT / "deploy" / "requirements.txt"),
        region=region,
        protocol="HTTP",
        deployment_type="direct_code_deploy",
        runtime_type="PYTHON_3_11",
        auto_create_execution_role=True,
        auto_create_s3=True,
        non_interactive=True,
    )

    print("Launching to AgentCore Runtime (packaging + upload + deploy) ...")
    result = rt.launch(env_vars={
        "AGENTCORE_MEMORY_ID": memory_id,
        "BEDROCK_MODEL_ID": model_id,
        "MEMORY_BACKEND": "agentcore",
    })

    agent_arn = getattr(result, "agent_arn", None) or getattr(result, "agent_runtime_arn", None)
    agent_id = getattr(result, "agent_id", None) or getattr(result, "agent_runtime_id", None)
    print("\nLaunch result fields:", {k: getattr(result, k) for k in dir(result)
                                      if not k.startswith("_") and not callable(getattr(result, k))})

    if agent_id:
        print("\nEnsuring execution role has Memory + Bedrock permissions ...")
        ensure_role_permissions(region, agent_id, memory_id)

    print("\n=== DEPLOYED ===")
    print(f"Agent runtime ARN: {agent_arn}")
    print("Set this in .env to route the website through the runtime:")
    print(f"  AGENT_MODE=runtime")
    print(f"  AGENTCORE_RUNTIME_ARN={agent_arn}")


if __name__ == "__main__":
    main()
