"""Deploy a serverless model endpoint in Azure AI Foundry (Azure ML workspace)."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from azure.identity import DefaultAzureCredential

try:
    from azure.ai.ml import MLClient
    from azure.ai.ml.entities import ServerlessEndpoint
except ImportError:
    MLClient = None  # type: ignore[assignment,misc]
    ServerlessEndpoint = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Supported models mapped to their Foundry/Azure AI model IDs.
# Keys are the display names shown in the workflow dropdown.
SUPPORTED_MODELS: dict[str, str] = {
    "gpt-4.1": "gpt-4.1",
    "gpt-4.1-mini": "gpt-4.1-mini",
    "gpt-5.4": "gpt-5.4",
    "gpt-5.4-mini": "gpt-5.4-mini",
    "claude-opus-4.5": "claude-opus-4-5",
    "claude-opus-5": "claude-opus-5",
    "claude-sonnet-4.5": "claude-sonnet-4-5",
}

POLL_INTERVAL_SECONDS = 15
DEPLOY_TIMEOUT_SECONDS = 600


def _get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable '{name}' is not set.")
    return value


def _wait_for_endpoint(
    *,
    ml_client: "MLClient",
    endpoint_name: str,
    timeout: int = DEPLOY_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Poll until endpoint provisioning state reaches a terminal state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ep = ml_client.serverless_endpoints.get(endpoint_name)
            state = str(getattr(ep, "provisioning_state", "")).lower()
            print(f"[deploy-model] Endpoint '{endpoint_name}' provisioning state: {state}")
            if state == "succeeded":
                scoring_uri = getattr(ep, "scoring_uri", None) or ""
                return True, f"Endpoint ready: {scoring_uri}"
            if state in {"failed", "canceled", "cancelled"}:
                return False, f"Endpoint provisioning failed with state: {state}"
        except Exception as exc:
            print(f"[deploy-model] Warning: status poll failed: {exc}")
        time.sleep(POLL_INTERVAL_SECONDS)
    return False, f"Timed out after {timeout}s waiting for endpoint '{endpoint_name}'"


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a serverless model to Azure AI Foundry")
    parser.add_argument(
        "--model",
        required=True,
        choices=list(SUPPORTED_MODELS.keys()),
        help="Model to deploy (must match a supported model name).",
    )
    parser.add_argument(
        "--endpoint-name",
        default=None,
        help="Custom endpoint name. Defaults to '<model-name>-endpoint'.",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Return immediately after creating the endpoint without waiting for provisioning.",
    )
    args = parser.parse_args()

    if MLClient is None or ServerlessEndpoint is None:
        print("[deploy-model] azure-ai-ml is not installed. Run: pip install -r requirements.txt")
        return 1

    try:
        subscription_id = _get_required_env("AZURE_SUBSCRIPTION_ID")
        resource_group = _get_required_env("AZURE_RESOURCE_GROUP")
        workspace_name = _get_required_env("AZURE_AI_FOUNDRY_PROJECT_NAME")
    except ValueError as exc:
        print(f"[deploy-model] Configuration error: {exc}")
        return 1

    model_id = SUPPORTED_MODELS[args.model]
    endpoint_name = args.endpoint_name or f"{args.model.replace('.', '-')}-endpoint"

    print(f"[deploy-model] Model: {args.model} (ID: {model_id})")
    print(f"[deploy-model] Endpoint name: {endpoint_name}")
    print(f"[deploy-model] Workspace: {workspace_name} / {resource_group} / {subscription_id}")

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    try:
        ml_client = MLClient(
            credential=credential,
            subscription_id=subscription_id,
            resource_group_name=resource_group,
            workspace_name=workspace_name,
        )
    except Exception as exc:
        print(f"[deploy-model] Could not create MLClient: {exc}")
        return 1

    # Check if endpoint already exists
    try:
        existing = ml_client.serverless_endpoints.get(endpoint_name)
        state = str(getattr(existing, "provisioning_state", "")).lower()
        if state == "succeeded":
            scoring_uri = getattr(existing, "scoring_uri", "")
            print(f"[deploy-model] Endpoint '{endpoint_name}' already exists and is ready.")
            print(f"[deploy-model] Scoring URI: {scoring_uri}")
            return 0
        print(f"[deploy-model] Endpoint '{endpoint_name}' exists with state '{state}' — waiting for completion.")
        if not args.no_wait:
            ok, message = _wait_for_endpoint(ml_client=ml_client, endpoint_name=endpoint_name)
            print(f"[deploy-model] {message}")
            return 0 if ok else 1
        return 0
    except Exception as exc:
        if "not found" not in str(exc).lower() and "404" not in str(exc):
            print(f"[deploy-model] Warning: could not check existing endpoint: {exc}")

    # Create new serverless endpoint
    print(f"[deploy-model] Creating serverless endpoint '{endpoint_name}' for model '{model_id}'...")
    try:
        endpoint = ServerlessEndpoint(
            name=endpoint_name,
            model_id=model_id,
        )
        ml_client.serverless_endpoints.begin_create_or_update(endpoint).result(
            timeout=30  # just start the operation; poll separately
        )
    except Exception as exc:
        # Some SDK versions return before the LRO completes — that is fine
        if "operation" not in str(exc).lower() and "timed out" not in str(exc).lower():
            print(f"[deploy-model] Failed to create endpoint: {exc}")
            return 1

    print(f"[deploy-model] Endpoint creation initiated.")

    if args.no_wait:
        print(f"[deploy-model] --no-wait: returning immediately. Check Foundry portal for status.")
        return 0

    ok, message = _wait_for_endpoint(ml_client=ml_client, endpoint_name=endpoint_name)
    print(f"[deploy-model] {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
