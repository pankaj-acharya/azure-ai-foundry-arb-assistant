# Agent notes for this repository

This project was built with the microsoft-foundry skill. Before working on or answering
questions about foundry agents, read the microsoft-foundry skill first.

## What's here

- `src/agent_prompts.py` — single source of truth for the 5 ARB agents' names/instructions
  (architecture, security, cost, resiliency specialists + `arb-summarizer-agent`).
- `scripts/orchestrate_persistent_agents.py` — the original orchestration script: runs the
  4 specialists in parallel via persistent Foundry agents, then the summarizer, writing
  `outputs/arb-review.md`. Deployed/managed via `.github/workflows/deploy-foundry-agents.yml`
  and `.github/workflows/orchestrate-arb-review.yml`.
- `src/agent_framework_workflow.py` + `src/hosted_arb_workflow.py` — a Microsoft Agent
  Framework hosted-workflow port of the same orchestration logic (see README's "Agent
  Framework hosted workflow" section for why: Foundry's portal Workflows tab is retiring
  Dec 1, 2026 and doesn't support persistent/hosted agents as nodes). Scaffolded as an
  `azd ai agent` hosted agent (`azure.yaml` service `arb-review-workflow-agent`,
  `host: azure.ai.agent`) targeting the existing Foundry project `proj_foundry_labs` under
  account `ArchReviewAssistant` (resource group `lab_arch_review_agent`), reusing the
  existing `gpt-5-4` model deployment.
