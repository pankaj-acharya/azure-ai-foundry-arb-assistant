"""Best-effort script to create/update Foundry agents where SDK support exists."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential

try:
    from azure.ai.projects import AIProjectClient
except ImportError:
    AIProjectClient = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent_prompts import (  # noqa: E402
    ARCHITECTURE_AGENT_PROMPT,
    COST_AGENT_PROMPT,
    RESILIENCY_AGENT_PROMPT,
    SECURITY_AGENT_PROMPT,
)
from src.config import load_settings  # noqa: E402

try:
    from azure.ai.projects.models import PromptAgentDefinition
except ImportError:
    PromptAgentDefinition = None


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


def _deploy_with_create_version(
    create_version: Callable[..., Any],
    model_name: str,
    definitions: dict[str, str],
) -> tuple[int, list[str]]:
    errors: list[str] = []
    success_count = 0
    for name, instructions in definitions.items():
        try:
            definition = _build_version_definition(model_name, instructions)
            create_version(
                agent_name=name,
                definition=definition,
            )
            print(f"[deploy] Created agent version with create_version: {name}")
            success_count += 1
        except Exception as exc:
            msg = f"create_version failed for '{name}': {exc}"
            print(f"[deploy] {msg}")
            errors.append(msg)
    return success_count, errors


def main() -> int:
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
        return 0

    definitions = {
        "architecture-agent": ARCHITECTURE_AGENT_PROMPT,
        "security-agent": SECURITY_AGENT_PROMPT,
        "cost-agent": COST_AGENT_PROMPT,
        "resiliency-agent": RESILIENCY_AGENT_PROMPT,
    }

    method_attempts: list[tuple[str, Callable[..., Any]]] = []
    create_or_update = getattr(agents_api, "create_or_update_agent", None)
    create_agent = getattr(agents_api, "create_agent", None)
    create_version = getattr(agents_api, "create_version", None)

    if callable(create_or_update):
        method_attempts.append(("create_or_update_agent", create_or_update))
    if callable(create_agent):
        method_attempts.append(("create_agent", create_agent))
    if callable(create_version):
        method_attempts.append(("create_version", create_version))

    if not method_attempts:
        available = [name for name in dir(agents_api) if not name.startswith("_")]
        print("[deploy] SDK agent API shape is unsupported for automated provisioning.")
        print(f"[deploy] Available agents API methods: {', '.join(available)}")
        print("[deploy] Fallback: Create agents manually in Azure AI Foundry portal using prompt text from src/agent_prompts.py.")
        return 0

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
