"""Best-effort script to create/update Foundry agents where SDK support exists."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential

try:
    from azure.ai.projects import AIProjectClient
except ImportError:
    AIProjectClient = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent_prompts import (  # noqa: E402
    ALL_PERSISTENT_AGENT_PROMPTS,
)
from src.config import load_settings  # noqa: E402

try:
    from azure.ai.projects.models import (
        AgentEndpointConfig,
        FixedRatioVersionSelectionRule,
        PromptAgentDefinition,
        ProtocolConfiguration,
        ResponsesProtocolConfiguration,
        VersionSelector,
    )
except ImportError:
    AgentEndpointConfig = None
    FixedRatioVersionSelectionRule = None
    PromptAgentDefinition = None
    ProtocolConfiguration = None
    ResponsesProtocolConfiguration = None
    VersionSelector = None


def _is_not_found_error(exc: Exception) -> bool:
    """Return True when the SDK exception represents HTTP 404."""

    status_code = getattr(exc, "status_code", None)
    if status_code == 404:
        return True

    status = getattr(exc, "status", None)
    if status == 404:
        return True

    return "404" in str(exc)


def _validate_project_endpoint_format(endpoint: str) -> tuple[bool, str]:
    """Validate Foundry endpoint shape expected by AIProjectClient."""

    if not endpoint.strip():
        return False, "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is empty"

    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        return False, "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT must use https"

    if not parsed.netloc.endswith(".services.ai.azure.com"):
        return False, "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT host must end with .services.ai.azure.com"

    if "/api/projects/" not in parsed.path:
        return (
            False,
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT must include '/api/projects/<project-name>' path segment",
        )

    return True, "Foundry project endpoint format is valid"


def _extract_http_status_code(exc: Exception) -> int | None:
    for attr_name in ("status_code", "status"):
        value = getattr(exc, attr_name, None)
        if isinstance(value, int):
            return value

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status
    return None


def _extract_service_error_code(exc: Exception) -> str | None:
    model = getattr(exc, "model", None)
    if model is not None:
        error_obj = getattr(model, "error", None)
        code = getattr(error_obj, "code", None)
        if isinstance(code, str) and code.strip():
            return code.strip()

    error_obj = getattr(exc, "error", None)
    code = getattr(error_obj, "code", None)
    if isinstance(code, str) and code.strip():
        return code.strip()
    return None


def _extract_request_id(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    for header_name in ("x-ms-request-id", "x-request-id", "x-ms-client-request-id"):
        try:
            header_value = headers.get(header_name)  # type: ignore[call-arg]
            if isinstance(header_value, str) and header_value.strip():
                return header_value.strip()
        except Exception:
            continue
    return None


def _format_exception_context(exc: Exception) -> str:
    status_code = _extract_http_status_code(exc)
    error_code = _extract_service_error_code(exc)
    request_id = _extract_request_id(exc)
    details = [f"error={str(exc).strip()}"]
    if status_code is not None:
        details.append(f"http_status={status_code}")
    if error_code:
        details.append(f"service_code={error_code}")
    if request_id:
        details.append(f"request_id={request_id}")
    return ", ".join(details)


def _is_permission_error(exc: Exception) -> bool:
    status_code = _extract_http_status_code(exc)
    if status_code in (401, 403):
        return True

    error_code = (_extract_service_error_code(exc) or "").lower()
    if error_code in {"permissiondenied", "forbidden", "authorizationfailed", "unauthorized"}:
        return True

    message = str(exc).lower()
    return "permission" in message or "authorization" in message or "forbidden" in message


def _is_preview_feature_error(exc: Exception) -> bool:
    error_code = (_extract_service_error_code(exc) or "").lower()
    if error_code == "preview_feature_required":
        return True
    return "preview_feature_required" in str(exc).lower()


def _deploy_with_create_or_update_agent(
    create_or_update: Callable[..., Any],
    model_name: str,
    definitions: dict[str, str],
) -> tuple[int, list[str]]:
    errors: list[str] = []
    success_count = 0
    for name, instructions in definitions.items():
        try:
            create_or_update(
                {
                    "name": name,
                    "model": model_name,
                    "instructions": instructions,
                }
            )
            print(f"[deploy] Upserted agent with create_or_update_agent: {name}")
            success_count += 1
        except Exception as exc:
            msg = f"create_or_update_agent failed for '{name}': {exc}"
            print(f"[deploy] {msg}")
            errors.append(msg)
    return success_count, errors


def _deploy_with_create_agent(
    create_agent: Callable[..., Any],
    model_name: str,
    definitions: dict[str, str],
) -> tuple[int, list[str]]:
    errors: list[str] = []
    success_count = 0
    for name, instructions in definitions.items():
        try:
            create_agent(
                model=model_name,
                name=name,
                instructions=instructions,
            )
            print(f"[deploy] Created agent with create_agent: {name}")
            success_count += 1
        except Exception as exc:
            msg = f"create_agent failed for '{name}': {exc}"
            print(f"[deploy] {msg}")
            errors.append(msg)
    return success_count, errors


def _build_version_definition(model_name: str, instructions: str) -> Any:
    if PromptAgentDefinition is not None:
        return PromptAgentDefinition(model=model_name, instructions=instructions)
    return {
        "model": model_name,
        "instructions": instructions,
    }


def _configure_agent_endpoint_to_latest_version(
    *,
    update_details: Callable[..., Any] | None,
    agent_name: str,
    agent_version: str,
) -> tuple[bool, str]:
    """Bind an agent endpoint to the newly created version using responses protocol."""

    required_models_available = all(
        value is not None
        for value in (
            AgentEndpointConfig,
            FixedRatioVersionSelectionRule,
            ProtocolConfiguration,
            ResponsesProtocolConfiguration,
            VersionSelector,
        )
    )
    if not callable(update_details) or not required_models_available:
        return False, "update_details or endpoint configuration models are unavailable in current SDK"

    endpoint_config = AgentEndpointConfig(
        version_selector=VersionSelector(
            version_selection_rules=[
                FixedRatioVersionSelectionRule(agent_version=agent_version, traffic_percentage=100),
            ]
        ),
        protocol_configuration=ProtocolConfiguration(responses=ResponsesProtocolConfiguration()),
    )
    update_details(agent_name=agent_name, agent_endpoint=endpoint_config)
    return True, "Endpoint configured to latest version"


def _deploy_with_create_version(
    create_version: Callable[..., Any],
    model_name: str,
    definitions: dict[str, str],
    update_details: Callable[..., Any] | None,
) -> tuple[int, list[str]]:
    errors: list[str] = []
    success_count = 0
    for name, instructions in definitions.items():
        try:
            definition = _build_version_definition(model_name, instructions)
            created = create_version(
                agent_name=name,
                definition=definition,
            )
            created_version = getattr(created, "version", None)
            if isinstance(created_version, str):
                endpoint_ok, endpoint_message = _configure_agent_endpoint_to_latest_version(
                    update_details=update_details,
                    agent_name=name,
                    agent_version=created_version,
                )
                if endpoint_ok:
                    print(f"[deploy] Endpoint configured with update_details: {name} -> v{created_version}")
                else:
                    print(f"[deploy] Warning: endpoint configuration skipped for '{name}': {endpoint_message}")
            else:
                print(f"[deploy] Warning: create_version response did not include version for '{name}'")
            print(f"[deploy] Created agent version with create_version: {name}")
            success_count += 1
        except Exception as exc:
            if _is_permission_error(exc):
                msg = (
                    f"create_version permission failure for '{name}'. "
                    f"Confirm Foundry role assignment for this principal. {_format_exception_context(exc)}"
                )
            elif _is_preview_feature_error(exc):
                msg = (
                    f"create_version preview feature gate for '{name}'. "
                    "Enable required Foundry preview features. "
                    f"{_format_exception_context(exc)}"
                )
            elif _is_not_found_error(exc):
                msg = (
                    f"create_version returned 404 for '{name}'. "
                    "This usually indicates wrong project endpoint scope or missing Foundry RBAC at project/resource scope. "
                    f"{_format_exception_context(exc)}"
                )
            else:
                msg = f"create_version failed for '{name}': {_format_exception_context(exc)}"
            print(f"[deploy] {msg}")
            errors.append(msg)
    return success_count, errors


def _preflight_chat_probe(client: Any, model_name: str) -> tuple[bool, str]:
    """Run a minimal inference probe to validate deployment visibility."""

    try:
        with client.get_openai_client() as openai_client:
            response = openai_client.responses.create(
                model=model_name,
                instructions="Reply with one word: ok",
                input="ping",
                max_output_tokens=16,
            )
            output_text = getattr(response, "output_text", None)
            if isinstance(output_text, str) and output_text.strip():
                return True, "Inference probe succeeded via get_openai_client().responses.create()"
            return True, "Inference probe succeeded via responses API"
    except Exception as exc:
        return False, f"Inference probe failed: {_format_exception_context(exc)}"


def _preflight_agents_list_probe(agents_api: Any) -> tuple[bool, str]:
    """Validate that agent APIs are reachable with current principal."""

    try:
        pager = agents_api.list(limit=1)
        for _ in pager:
            break
        return True, "Agents list probe succeeded"
    except Exception as exc:
        return False, f"Agents list probe failed: {_format_exception_context(exc)}"


def _preflight_create_version_probe(
    *,
    create_version: Callable[..., Any] | None,
    delete_version: Callable[..., Any] | None,
    delete_agent: Callable[..., Any] | None,
    model_name: str,
) -> tuple[bool, str]:
    """Attempt a temporary create_version to verify agent publish authorization."""

    if not callable(create_version):
        return False, "create_version API is not available in current SDK surface"

    probe_agent_name = f"ci-probe-{uuid.uuid4().hex[:10]}"
    created_version = None
    try:
        created_version = create_version(
            agent_name=probe_agent_name,
            definition=_build_version_definition(model_name, "Respond with the exact word: ok"),
            description="CI probe for Foundry publish capability",
        )
        version = getattr(created_version, "version", "<unknown>")
        return True, f"create_version probe succeeded for '{probe_agent_name}' version '{version}'"
    except Exception as exc:
        return False, f"create_version probe failed: {_format_exception_context(exc)}"
    finally:
        if created_version is not None:
            version = getattr(created_version, "version", None)
            if callable(delete_version) and isinstance(version, str):
                try:
                    delete_version(agent_name=probe_agent_name, agent_version=version, force=True)
                except Exception as cleanup_exc:
                    print(f"[preflight] Warning: probe version cleanup failed: {_format_exception_context(cleanup_exc)}")
            if callable(delete_agent):
                try:
                    delete_agent(agent_name=probe_agent_name, force=True)
                except Exception as cleanup_exc:
                    print(f"[preflight] Warning: probe agent cleanup failed: {_format_exception_context(cleanup_exc)}")


def _run_preflight_checks(
    *,
    settings: Any,
    client: Any,
    agents_api: Any,
    create_or_update: Callable[..., Any] | None,
    create_agent: Callable[..., Any] | None,
    create_version: Callable[..., Any] | None,
    delete_version: Callable[..., Any] | None,
    delete_agent: Callable[..., Any] | None,
) -> tuple[bool, list[str]]:
    """Validate endpoint, model deployment, and agent API readiness before deploy."""

    errors: list[str] = []

    endpoint_ok, endpoint_message = _validate_project_endpoint_format(settings.azure_ai_foundry_project_endpoint)
    if endpoint_ok:
        print(f"[preflight] {endpoint_message}")
    else:
        errors.append(endpoint_message)

    if not settings.azure_ai_foundry_model_deployment_name:
        errors.append("AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT_NAME is empty")

    if not any(callable(method) for method in (create_or_update, create_agent, create_version)):
        errors.append("No supported agent provisioning method available in current SDK")

    probe_ok, probe_message = _preflight_chat_probe(client, settings.azure_ai_foundry_model_deployment_name)
    if probe_ok:
        print(f"[preflight] {probe_message}")
    else:
        errors.append(probe_message)

    list_ok, list_message = _preflight_agents_list_probe(agents_api)
    if list_ok:
        print(f"[preflight] {list_message}")
    else:
        errors.append(list_message)

    version_probe_ok, version_probe_message = _preflight_create_version_probe(
        create_version=create_version,
        delete_version=delete_version,
        delete_agent=delete_agent,
        model_name=settings.azure_ai_foundry_model_deployment_name,
    )
    if version_probe_ok:
        print(f"[preflight] {version_probe_message}")
    else:
        errors.append(version_probe_message)

    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy or validate Foundry prompt agents")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run endpoint/model/SDK checks only and exit.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail (non-zero exit) when automated provisioning cannot complete.",
    )
    args = parser.parse_args()

    settings = load_settings()

    if AIProjectClient is None:
        print("[deploy] azure-ai-projects is not installed. Run: pip install -r requirements.txt")
        return 1

    try:
        client = AIProjectClient(
            endpoint=settings.azure_ai_foundry_project_endpoint,
            credential=DefaultAzureCredential(exclude_interactive_browser_credential=False),
        )
    except Exception as exc:
        print(f"[deploy] Could not connect to Azure AI Foundry project: {exc}")
        return 1

    agents_api = getattr(client, "agents", None)
    if agents_api is None:
        print("[deploy] Current SDK does not support persistent agent management on AIProjectClient.")
        print("[deploy] Fallback: Keep prompts in src/agent_prompts.py and use local orchestration mode.")
        return 1 if args.strict else 0

    definitions = dict(ALL_PERSISTENT_AGENT_PROMPTS)

    method_attempts: list[tuple[str, Callable[..., Any]]] = []
    create_or_update = getattr(agents_api, "create_or_update_agent", None)
    create_agent = getattr(agents_api, "create_agent", None)
    create_version = getattr(agents_api, "create_version", None)
    update_details = getattr(agents_api, "update_details", None)
    delete_version = getattr(agents_api, "delete_version", None)
    delete_agent = getattr(agents_api, "delete", None)

    if callable(create_or_update):
        method_attempts.append(("create_or_update_agent", create_or_update))
    if callable(create_agent):
        method_attempts.append(("create_agent", create_agent))
    if callable(create_version):
        method_attempts.append(("create_version", create_version))

    preflight_ok, preflight_errors = _run_preflight_checks(
        settings=settings,
        client=client,
        agents_api=agents_api,
        create_or_update=create_or_update,
        create_agent=create_agent,
        create_version=create_version,
        delete_version=delete_version,
        delete_agent=delete_agent,
    )
    if not preflight_ok:
        print("[preflight] Validation failed:")
        for err in preflight_errors:
            print(f"[preflight] - {err}")
        return 1

    print("[preflight] Validation passed.")
    if args.preflight_only:
        print("[preflight] Preflight-only mode complete.")
        return 0

    if not method_attempts:
        available = [name for name in dir(agents_api) if not name.startswith("_")]
        print("[deploy] SDK agent API shape is unsupported for automated provisioning.")
        print(f"[deploy] Available agents API methods: {', '.join(available)}")
        print("[deploy] Fallback: Create agents manually in Azure AI Foundry portal using prompt text from src/agent_prompts.py.")
        return 1 if args.strict else 0

    for method_name, method in method_attempts:
        print(f"[deploy] Trying SDK method: {method_name}")
        if method_name == "create_or_update_agent":
            success_count, errors = _deploy_with_create_or_update_agent(
                method,
                settings.azure_ai_foundry_model_deployment_name,
                definitions,
            )
        elif method_name == "create_agent":
            success_count, errors = _deploy_with_create_agent(
                method,
                settings.azure_ai_foundry_model_deployment_name,
                definitions,
            )
        else:
            success_count, errors = _deploy_with_create_version(
                method,
                settings.azure_ai_foundry_model_deployment_name,
                definitions,
                update_details,
            )

        if success_count > 0:
            print(f"[deploy] Method '{method_name}' succeeded for {success_count} agent(s).")
            print("[deploy] Finished agent deployment attempt.")
            return 0

        print(f"[deploy] Method '{method_name}' could not provision agents.")
        if errors:
            print(f"[deploy] First error from '{method_name}': {errors[0]}")

    print("[deploy] No supported SDK method succeeded in this environment.")
    print("[deploy] Fallback: Create agents manually in Azure AI Foundry portal using prompt text from src/agent_prompts.py.")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
