"""Best-effort script to create/update Foundry agents where SDK support exists."""

from __future__ import annotations

import sys
from pathlib import Path

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

    create_or_update = getattr(agents_api, "create_or_update_agent", None)
    if create_or_update is None:
        print("[deploy] SDK agent API shape is unsupported for automated provisioning.")
        print("[deploy] Fallback: Create agents manually in Azure AI Foundry portal using prompt text from src/agent_prompts.py.")
        return 0

    definitions = {
        "architecture-agent": ARCHITECTURE_AGENT_PROMPT,
        "security-agent": SECURITY_AGENT_PROMPT,
        "cost-agent": COST_AGENT_PROMPT,
        "resiliency-agent": RESILIENCY_AGENT_PROMPT,
    }

    for name, instructions in definitions.items():
        try:
            create_or_update(
                {
                    "name": name,
                    "model": settings.azure_ai_foundry_model_deployment_name,
                    "instructions": instructions,
                }
            )
            print(f"[deploy] Upserted agent: {name}")
        except Exception as exc:
            print(f"[deploy] Could not upsert '{name}': {exc}")

    print("[deploy] Finished agent deployment attempt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
