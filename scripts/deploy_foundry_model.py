"""Deploy a model in Azure AI Foundry (new-style Account-based project).

Uses `az cognitiveservices account deployment create` (official CLI) which
correctly handles provider-specific schema requirements for Anthropic/OpenAI.
Account name is parsed automatically from AZURE_AI_FOUNDRY_PROJECT_ENDPOINT.
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

# Supported models: display name → deployment config.
SUPPORTED_MODELS: dict[str, dict[str, str]] = {
    "gpt-5":             {"name": "gpt-5",            "format": "OpenAI"},
    "gpt-5-mini":        {"name": "gpt-5-mini",       "format": "OpenAI"},
    "gpt-5.4":           {"name": "gpt-5.4",          "format": "OpenAI"},
    "gpt-5.4-mini":      {"name": "gpt-5.4-mini",     "format": "OpenAI"},
    "gpt-4o":            {"name": "gpt-4o",           "format": "OpenAI"},
    "gpt-4.1":           {"name": "gpt-4.1",          "format": "OpenAI"},
    "gpt-4.1-mini":      {"name": "gpt-4.1-mini",     "format": "OpenAI"},
    "claude-opus-4.5":   {"name": "claude-opus-4-5",  "format": "Anthropic"},
    "claude-opus-5":     {"name": "claude-opus-5",    "format": "Anthropic"},
    "claude-sonnet-4.5": {"name": "claude-sonnet-4-5","format": "Anthropic"},
}

MGMT_API_VERSION = "2025-04-01-preview"
POLL_INTERVAL_SECONDS = 15
DEPLOY_TIMEOUT_SECONDS = 600


def _get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable '{name}' is not set.")
    return value


def _extract_account_name(endpoint: str) -> str:
    """Parse account name from https://<account>.services.ai.azure.com/..."""
    match = re.match(r"https?://([^.]+)\.services\.ai\.azure\.com", endpoint.strip())
    if not match:
        raise ValueError(
            f"Cannot parse account name from endpoint: {endpoint!r}. "
            "Expected: https://<account>.services.ai.azure.com/..."
        )
    return match.group(1)


def _az(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["az", *args], capture_output=True, text=True)


def _az_rest(method: str, url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["az", "rest", "--method", method, "--url", url,
         "--headers", "Content-Type=application/json"],
        capture_output=True, text=True
    )


def _list_and_check_existing(
    subscription_id: str, resource_group: str, account_name: str, deployment_name: str
) -> str | None:
    """List all deployments and return state for deployment_name (or None if absent)."""
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/deployments?api-version={MGMT_API_VERSION}"
    )
    result = _az_rest("GET", url)
    if result.returncode != 0:
        print(f"[deploy-model] Warning: could not list deployments: {(result.stderr or result.stdout).strip()[:300]}")
        return None
    try:
        data = json.loads(result.stdout)
        deployments = data.get("value", [])
        print(f"[deploy-model] Existing deployments in account ({len(deployments)}):")
        for d in deployments:
            dname = d.get("name", "?")
            dstate = d.get("properties", {}).get("provisioningState", "?")
            dmodel = d.get("properties", {}).get("model", {}).get("name", "?")
            print(f"  - {dname}  model={dmodel}  state={dstate}")
            if dname.lower() == deployment_name.lower():
                return str(dstate).lower()
        return None
    except (json.JSONDecodeError, AttributeError):
        return None


def _get_available_model_version(
    resource_group: str, account_name: str, model_format: str, model_name: str
) -> str | None:
    """Query the account's available models and return the latest non-deprecated version."""
    result = subprocess.run(
        ["az", "cognitiveservices", "account", "list-models",
         "--resource-group", resource_group, "--name", account_name, "--output", "json"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[deploy-model] Warning: list-models failed: {result.stderr.strip()[:200]}")
        return None
    try:
        models = json.loads(result.stdout)
        print(f"[deploy-model] Available models in catalog ({len(models)}):")
        for m in models:
            mfmt = m.get("format") or m.get("modelFormat") or "?"
            mname = m.get("name") or m.get("modelName") or "?"
            mver = m.get("version") or m.get("modelVersion") or "?"
            mlc = m.get("lifecycleStatus") or m.get("status") or "?"
            print(f"  catalog: {mfmt}/{mname}  version={mver}  lifecycle={mlc}")

        # Collect all matching (version, lifecycleStatus) pairs
        DEPRECATED_STATES = {"deprecated", "deprecating", "retiring", "retired"}
        candidates: list[tuple[str, str]] = []
        for m in models:
            fmt = (m.get("format") or m.get("modelFormat") or "").lower()
            name = (m.get("name") or m.get("modelName") or "").lower()
            if fmt != model_format.lower() or name != model_name.lower():
                continue
            version = (
                m.get("version") or m.get("modelVersion") or
                (m.get("deprecation") or {}).get("fineTune") or ""
            )
            if not version:
                versions = m.get("versions") or m.get("modelVersions") or []
                version = str(versions[-1]) if versions else ""
            lc = (m.get("lifecycleStatus") or m.get("status") or "").lower()
            if version:
                candidates.append((version, lc))

        if not candidates:
            print(f"[deploy-model] Warning: {model_format}/{model_name} not found in catalog")
            return None

        # Prefer non-deprecated; fail cleanly if all are deprecated
        active = [(v, lc) for v, lc in candidates if lc not in DEPRECATED_STATES]
        if not active:
            versions_str = ", ".join(v for v, _ in candidates)
            print(
                f"[deploy-model] ERROR: all versions of {model_format}/{model_name} are deprecated "
                f"({versions_str}) and cannot be used for new deployments.\n"
                f"[deploy-model] Please choose a model with GenerallyAvailable lifecycle status "
                f"(e.g. gpt-5.4, gpt-5.4-mini, gpt-5)."
            )
            return None

        pool = sorted(active, key=lambda x: x[0])
        chosen_version, chosen_lc = pool[-1]
        print(f"[deploy-model] Found version: {chosen_version}  lifecycle={chosen_lc}")
        return chosen_version

    except (json.JSONDecodeError, AttributeError, IndexError) as exc:
        print(f"[deploy-model] Warning: failed to parse list-models output: {exc}")
        return None


def _create_via_cli(
    subscription_id: str, resource_group: str, account_name: str,
    deployment_name: str, model_info: dict[str, str],
    model_version: str,
) -> bool:
    """Create deployment using `az cognitiveservices account deployment create`."""
    org_name = os.getenv("AZURE_ORG_NAME", "MyOrganisation").strip()
    country_code = os.getenv("AZURE_ORG_COUNTRY_CODE", "US").strip()
    industry = os.getenv("AZURE_ORG_INDUSTRY", "Technology").strip()

    cmd = [
        "az", "cognitiveservices", "account", "deployment", "create",
        "--subscription", subscription_id,
        "--resource-group", resource_group,
        "--name", account_name,
        "--deployment-name", deployment_name,
        "--model-format", model_info["format"],
        "--model-name", model_info["name"],
        "--model-version", model_version,
        "--sku-name", "Standard",
        "--sku-capacity", "1",
    ]

    if model_info["format"] == "Anthropic":
        # Pass provider data as a JSON blob.  Azure CLI sends it correctly.
        provider_data = json.dumps({
            "organizationName": org_name,
            "countryCode": country_code,
            "industry": industry,
        })
        cmd += ["--model-provider-data", provider_data]
        print(f"[deploy-model] ModelProviderData: org={org_name} country={country_code} industry={industry}")

    print(f"[deploy-model] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if result.returncode != 0:
        # Check if --model-provider-data flag is unknown in this CLI version
        if "model-provider-data" in stderr and "unrecognized" in stderr.lower():
            print("[deploy-model] --model-provider-data not supported by this CLI version. Retrying without it...")
            cmd = [c for c in cmd if c not in ("--model-provider-data", provider_data)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

        if result.returncode != 0:
            print(f"[deploy-model] CLI deployment create failed:\n{stderr or stdout}")
            return False

    if stdout:
        print(f"[deploy-model] Response: {stdout[:500]}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a model to Azure AI Foundry Account")
    parser.add_argument("--model", required=True, choices=list(SUPPORTED_MODELS.keys()))
    parser.add_argument("--endpoint-name", default=None,
                        help="Custom deployment name. Defaults to model name with dots→hyphens.")
    parser.add_argument("--no-wait", action="store_true",
                        help="Return immediately after submitting the create request.")
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

    print(f"[deploy-model] Model:      {args.model}  (format: {model_info['format']}, id: {model_info['name']})")
    print(f"[deploy-model] Deployment: {deployment_name}")
    print(f"[deploy-model] Account:    {account_name}  RG: {resource_group}")

    # List existing + idempotency check
    state = _list_and_check_existing(subscription_id, resource_group, account_name, deployment_name)
    if state == "succeeded":
        print(f"[deploy-model] Deployment '{deployment_name}' already exists and is ready. Nothing to do.")
        return 0
    if state in ("creating", "running", "updating", "accepted"):
        print(f"[deploy-model] Deployment '{deployment_name}' already in progress (state: '{state}').")
        if args.no_wait:
            return 0
        # Fall through to polling

    if state is None:
        # Discover the model version from the account's model catalog
        model_version = _get_available_model_version(
            resource_group, account_name, model_info["format"], model_info["name"]
        )
        if not model_version:
            print(
                f"[deploy-model] ERROR: {model_info['format']}/{model_info['name']} is not available "
                f"in account '{account_name}'.\n"
                f"[deploy-model] Possible reasons:\n"
                f"[deploy-model]   1. Anthropic models require marketplace acceptance — go to Azure AI Foundry\n"
                f"[deploy-model]      portal → Model Catalog → Search '{model_info['name']}' → Subscribe\n"
                f"[deploy-model]   2. The model may not be available in your Azure region (East US)\n"
                f"[deploy-model]   3. Your subscription may need Anthropic model access enabled by your admin\n"
                f"[deploy-model] Tip: Run this workflow with a GPT model (e.g., gpt-4.1-mini) to verify the pipeline works."
            )
            return 1
        print(f"[deploy-model] Creating deployment '{deployment_name}' (version: {model_version})...")
        if not _create_via_cli(
            subscription_id, resource_group, account_name, deployment_name, model_info, model_version
        ):
            return 1
        print(f"[deploy-model] Create request submitted.")

    if args.no_wait:
        print("[deploy-model] --no-wait: check Azure portal for provisioning status.")
        return 0

    # Poll until terminal state
    print(f"[deploy-model] Polling (timeout {DEPLOY_TIMEOUT_SECONDS}s)...")
    deadline = time.monotonic() + DEPLOY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        state = _list_and_check_existing(subscription_id, resource_group, account_name, deployment_name)
        print(f"[deploy-model] State: {state}")
        if state == "succeeded":
            print(f"[deploy-model] Deployment '{deployment_name}' is ready.")
            return 0
        if state in ("failed", "canceled", "cancelled"):
            print(f"[deploy-model] Deployment failed (state: {state}).")
            return 1
        if state is None:
            print("[deploy-model] Deployment not found during poll - it may have just been created.")
        time.sleep(POLL_INTERVAL_SECONDS)

    print(f"[deploy-model] Timed out after {DEPLOY_TIMEOUT_SECONDS}s. Check Azure portal.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
