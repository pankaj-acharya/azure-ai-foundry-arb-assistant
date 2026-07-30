"""Deploy a model in Azure AI Foundry (new-style Account-based project).

Uses the CognitiveServices ARM REST API via `az rest` — no azure-ai-ml SDK needed.
Account name is parsed from AZURE_AI_FOUNDRY_PROJECT_ENDPOINT automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Supported models: display name → ARM deployment config.
# format: "OpenAI" for GPT models, "Anthropic" for Claude models.
SUPPORTED_MODELS: dict[str, dict[str, str]] = {
    "gpt-4.1":          {"name": "gpt-4.1",          "format": "OpenAI"},
    "gpt-4.1-mini":     {"name": "gpt-4.1-mini",     "format": "OpenAI"},
    "gpt-5.4":          {"name": "gpt-5.4",          "format": "OpenAI"},
    "gpt-5.4-mini":     {"name": "gpt-5.4-mini",     "format": "OpenAI"},
    "claude-opus-4.5":  {"name": "claude-opus-4-5",  "format": "Anthropic"},
    "claude-opus-5":    {"name": "claude-opus-5",    "format": "Anthropic"},
    "claude-sonnet-4.5":{"name": "claude-sonnet-4-5","format": "Anthropic"},
}

# CognitiveServices management API version
MGMT_API_VERSION = "2025-04-01-preview"

POLL_INTERVAL_SECONDS = 15
DEPLOY_TIMEOUT_SECONDS = 600


def _get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable '{name}' is not set.")
    return value


def _extract_account_name(endpoint: str) -> str:
    """Parse the AI Services account name from the project endpoint URL.

    Expected format: https://<account>.services.ai.azure.com/api/projects/<project>
    """
    match = re.match(r"https?://([^.]+)\.services\.ai\.azure\.com", endpoint.strip())
    if not match:
        raise ValueError(
            f"Cannot parse account name from endpoint: {endpoint!r}. "
            "Expected format: https://<account>.services.ai.azure.com/..."
        )
    return match.group(1)


def _az_rest(method: str, url: str, body: dict | None = None) -> subprocess.CompletedProcess:
    cmd = ["az", "rest", "--method", method, "--url", url,
           "--headers", "Content-Type=application/json"]
    if body is not None:
        cmd += ["--body", json.dumps(body)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _get_deployment_state(
    subscription_id: str, resource_group: str, account_name: str, deployment_name: str
) -> str | None:
    """Return lower-case provisioning state, or None if not found."""
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/deployments/{deployment_name}?api-version={MGMT_API_VERSION}"
    )
    result = _az_rest("GET", url)
    if result.returncode != 0:
        stderr = result.stderr or result.stdout
        if "ResourceNotFound" in stderr or "404" in stderr or "NotFound" in stderr:
            return None
        print(f"[deploy-model] Warning: GET deployment returned error: {stderr.strip()[:200]}")
        return None
    try:
        data = json.loads(result.stdout)
        return str(data.get("properties", {}).get("provisioningState", "")).lower()
    except (json.JSONDecodeError, AttributeError):
        return None


def _create_deployment(
    subscription_id: str, resource_group: str, account_name: str,
    deployment_name: str, model_info: dict[str, str],
) -> bool:
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/deployments/{deployment_name}?api-version={MGMT_API_VERSION}"
    )
    body: dict = {
        "sku": {"name": "Standard", "capacity": 1},
        "properties": {
            "model": {
                "format": model_info["format"],
                "name": model_info["name"],
            }
        },
    }
    # Anthropic model deployments require ModelProviderData at the ROOT level.
    if model_info["format"] == "Anthropic":
        org_name = os.getenv("AZURE_ORG_NAME", "MyOrganisation").strip()
        country_code = os.getenv("AZURE_ORG_COUNTRY_CODE", "US").strip()
        industry = os.getenv("AZURE_ORG_INDUSTRY", "Technology").strip()
        body["modelProviderData"] = {
            "organizationName": org_name,
            "countryCode": country_code,
            "industry": industry,
        }
        print(
            f"[deploy-model] ModelProviderData: "
            f"org={org_name}, country={country_code}, industry={industry}"
        )
    print(f"[deploy-model] Request body: {json.dumps(body, indent=2)}")
    result = _az_rest("PUT", url, body)
    if result.returncode != 0:
        print(f"[deploy-model] Failed to create deployment:\n{(result.stderr or result.stdout).strip()}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a model to Azure AI Foundry Account")
    parser.add_argument(
        "--model", required=True, choices=list(SUPPORTED_MODELS.keys()),
        help="Model to deploy (display name from the workflow dropdown).",
    )
    parser.add_argument(
        "--endpoint-name", default=None,
        help="Custom deployment name. Defaults to '<model-name>' with dots replaced by hyphens.",
    )
    parser.add_argument(
        "--no-wait", action="store_true",
        help="Return immediately after submitting the create request.",
    )
    args = parser.parse_args()

    try:
        subscription_id = _get_required_env("AZURE_SUBSCRIPTION_ID")
        resource_group = _get_required_env("AZURE_RESOURCE_GROUP")
        project_endpoint = _get_required_env("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
        account_name = _extract_account_name(project_endpoint)
    except ValueError as exc:
        print(f"[deploy-model] Configuration error: {exc}")
        return 1

    model_info = SUPPORTED_MODELS[args.model]
    deployment_name = args.endpoint_name or args.model.replace(".", "-")

    print(f"[deploy-model] Model:       {args.model} (format: {model_info['format']}, name: {model_info['name']})")
    print(f"[deploy-model] Deployment:  {deployment_name}")
    print(f"[deploy-model] Account:     {account_name}  RG: {resource_group}")

    # --- Idempotency check ---
    state = _get_deployment_state(subscription_id, resource_group, account_name, deployment_name)
    if state == "succeeded":
        print(f"[deploy-model] Deployment '{deployment_name}' already exists and is ready. Nothing to do.")
        return 0
    if state in ("creating", "running", "updating", "accepted"):
        print(f"[deploy-model] Deployment '{deployment_name}' already in progress (state: '{state}').")
        if args.no_wait:
            return 0
        # Fall through to polling loop below

    if state is None:
        print(f"[deploy-model] Creating deployment '{deployment_name}'...")
        if not _create_deployment(
            subscription_id, resource_group, account_name, deployment_name, model_info
        ):
            return 1
        print(f"[deploy-model] Create request accepted.")

    if args.no_wait:
        print(f"[deploy-model] --no-wait set. Check Azure portal for provisioning status.")
        return 0

    # --- Poll until terminal state ---
    print(f"[deploy-model] Polling provisioning state (timeout {DEPLOY_TIMEOUT_SECONDS}s)...")
    deadline = time.monotonic() + DEPLOY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        state = _get_deployment_state(subscription_id, resource_group, account_name, deployment_name)
        print(f"[deploy-model] State: {state}")
        if state == "succeeded":
            print(f"[deploy-model] ✓ Deployment '{deployment_name}' is ready.")
            return 0
        if state in ("failed", "canceled", "cancelled"):
            print(f"[deploy-model] ✗ Deployment failed with state: {state}")
            return 1
        time.sleep(POLL_INTERVAL_SECONDS)

    print(f"[deploy-model] Timed out after {DEPLOY_TIMEOUT_SECONDS}s. Check Azure portal for final state.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
