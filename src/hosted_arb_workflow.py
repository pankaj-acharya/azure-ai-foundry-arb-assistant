"""Hosting entrypoint for the ARB review Agent Framework workflow.

Run locally with:

    python -m src.hosted_arb_workflow

or via the Foundry hosted-agent tooling (``azd ai agent run`` /
``azd ai agent invoke``) once deployed as a Foundry hosted agent. Requires
``AZURE_AI_PROJECT_ENDPOINT`` to be set to the Foundry project endpoint whose
agents were deployed by ``.github/workflows/deploy-foundry-agents.yml``.
"""

from __future__ import annotations

import logging

from agent_framework_foundry_hosting import ResponsesHostServer

from src.agent_framework_workflow import build_arb_workflow_agent

logging.basicConfig(level=logging.INFO)


def main() -> None:
    agent = build_arb_workflow_agent()
    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()
