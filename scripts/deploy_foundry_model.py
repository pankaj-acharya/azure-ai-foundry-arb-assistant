"""Deploy a model in Azure AI Foundry (new-style Account-based project).

Uses `az cognitiveservices account deployment create` for OpenAI models.
Anthropic models use direct ARM REST API (az rest) because the runner's CLI
may not have the --model-provider-data flag available.
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
    # Anthropic — names must match az cognitiveservices account list-models output exactly
    "claude-haiku-4.5":  {"name": "claude-haiku-4-5", "format": "Anthropic"},
    "claude-opus-4.8":   {"name": "claude-opus-4-8",  "format": "Anthropic"},
    "claude-sonnet-5":   {"name": "claude-sonnet-5",  "format": "Anthropic"},
    "claude-opus-5":     {"name": "claude-opus-5",    "format": "Anthropic"},
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
) -> tuple[str, str] | None:
    """Return (version, sku_name) for the latest non-deprecated version, or None."""
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
            skus = [s.get("name", "?") for s in (m.get("skus") or [])]
            print(f"  catalog: {mfmt}/{mname}  version={mver}  lifecycle={mlc}  skus={skus}")

        DEPRECATED_STATES = {"deprecated", "deprecating", "retiring", "retired"}
        candidates: list[tuple[str, str, str]] = []  # (version, lifecycle, sku)
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
            # Extract first supported SKU; default to GlobalStandard for newer models
            sku_list = m.get("skus") or []
            sku_name = sku_list[0].get("name", "GlobalStandard") if sku_list else "GlobalStandard"
            if version:
                candidates.append((version, lc, sku_name))

        if not candidates:
            print(f"[deploy-model] Warning: {model_format}/{model_name} not found in catalog")
            return None

        active = [(v, lc, s) for v, lc, s in candidates if lc not in DEPRECATED_STATES]
        if not active:
            versions_str = ", ".join(v for v, _, _ in candidates)
            print(
                f"[deploy-model] ERROR: all versions of {model_format}/{model_name} are deprecated "
                f"({versions_str}). Please choose a GenerallyAvailable model (e.g. gpt-5.4, gpt-5)."
            )
            return None

        pool = sorted(active, key=lambda x: x[0])
        chosen_version, chosen_lc, chosen_sku = pool[-1]
        print(f"[deploy-model] Found version: {chosen_version}  lifecycle={chosen_lc}  sku={chosen_sku}")
        return chosen_version, chosen_sku

    except (json.JSONDecodeError, AttributeError, IndexError) as exc:
        print(f"[deploy-model] Warning: failed to parse list-models output: {exc}")
        return None


def _create_anthropic_via_rest(
    subscription_id: str, resource_group: str, account_name: str,
    deployment_name: str, model_info: dict[str, str],
    model_version: str, sku_name: str,
) -> bool:
    """Create an Anthropic deployment via az rest (bypasses CLI --model-provider-data flag).

    The GitHub Actions runner's Azure CLI may not support --model-provider-data.
    We construct the ARM PUT body ourselves, trying multiple schema variants across
    API versions until one succeeds.
    """
    org_name = os.getenv("AZURE_ORG_NAME", "MyOrganisation").strip()
    country_code = os.getenv("AZURE_ORG_COUNTRY_CODE", "US").strip()
    industry = os.getenv("AZURE_ORG_INDUSTRY", "Technology").strip()

    print(f"[deploy-model] Anthropic REST deploy: org={org_name} country={country_code} industry={industry}")

    base_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/deployments/{deployment_name}"
    )

    provider_data = {
        "organizationName": org_name,
        "countryCode": country_code,
        "industry": industry,
    }
    model_block = {
        "format": model_info["format"],
        "name": model_info["name"],
        "version": model_version,
    }
    sku_block = {"name": sku_name, "capacity": 1}

    # Try different API versions and body schemas — newest stable API first.
    # Each entry: (api_version, variant_label, body_dict)
    attempts = [
        ("2026-05-01", "root-object", {
            "sku": sku_block,
            "properties": {"model": model_block},
            "modelProviderData": provider_data,
        }),
        ("2026-03-01", "root-object", {
            "sku": sku_block,
            "properties": {"model": model_block},
            "modelProviderData": provider_data,
        }),
        ("2026-05-01", "props-object", {
            "sku": sku_block,
            "properties": {"model": model_block, "modelProviderData": provider_data},
        }),
        ("2026-03-01", "props-object", {
            "sku": sku_block,
            "properties": {"model": model_block, "modelProviderData": provider_data},
        }),
        ("2025-04-01-preview", "props-string", {
            "sku": sku_block,
            "properties": {"model": model_block, "modelProviderData": json.dumps(provider_data)},
        }),
    ]

    for api_version, variant, body in attempts:
        url = f"{base_url}?api-version={api_version}"
        body_json = json.dumps(body)
        print(f"[deploy-model] az rest PUT api={api_version} schema={variant}...")
        result = subprocess.run(
            ["az", "rest", "--method", "PUT", "--url", url,
             "--body", body_json, "--headers", "Content-Type=application/json"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            resp = result.stdout.strip()
            print(f"[deploy-model] REST PUT succeeded (api={api_version} schema={variant})")
            if resp:
                print(f"[deploy-model] Response: {resp[:300]}")
            return True
        stderr = result.stderr.strip()
        print(f"[deploy-model] REST PUT failed (api={api_version} schema={variant}): {stderr[:300]}")
        # Wrong API version — skip remaining variants for this version
        if "NoRegisteredProviderFound" in stderr or "could not find api-version" in stderr.lower():
            continue

    return False


def _create_via_cli(
    subscription_id: str, resource_group: str, account_name: str,
    deployment_name: str, model_info: dict[str, str],
    model_version: str,
    sku_name: str = "GlobalStandard",
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
        "--sku-name", sku_name,
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
        version_info = _get_available_model_version(
            resource_group, account_name, model_info["format"], model_info["name"]
        )
        if not version_info:
            print(
                f"[deploy-model] ERROR: {model_info['format']}/{model_info['name']} is not available "
                f"in account '{account_name}'.\n"
                f"[deploy-model] Possible reasons:\n"
                f"[deploy-model]   1. Anthropic models require marketplace acceptance — go to Azure AI Foundry\n"
                f"[deploy-model]      portal → Model Catalog → Search '{model_info['name']}' → Subscribe\n"
                f"[deploy-model]   2. The model may not be available in your Azure region (East US)\n"
                f"[deploy-model]   3. Your subscription may need Anthropic model access enabled by your admin\n"
                f"[deploy-model] Tip: Run this workflow with a GPT model (e.g., gpt-5.4, gpt-5) to verify the pipeline works."
            )
            return 1
        model_version, sku_name = version_info
        print(f"[deploy-model] Creating deployment '{deployment_name}' (version: {model_version}, sku: {sku_name})...")
        # Anthropic models require modelProviderData — the runner's CLI may not support
        # the --model-provider-data flag, so we use direct ARM REST for Anthropic.
        if model_info["format"] == "Anthropic":
            ok = _create_anthropic_via_rest(
                subscription_id, resource_group, account_name, deployment_name,
                model_info, model_version, sku_name,
            )
        else:
            ok = _create_via_cli(
                subscription_id, resource_group, account_name, deployment_name,
                model_info, model_version, sku_name,
            )
        if not ok:
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
