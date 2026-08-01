"""Microsoft Agent Framework port of the ARB orchestration logic.

Foundry's portal "Workflows" tab is a preview feature being retired on
December 1, 2026, and it does not support hosted/persistent agents as nodes.
This module ports the same review logic implemented in
``scripts/orchestrate_persistent_agents.py`` (4 specialist agents running in
parallel, fanned in to the summarizer agent) to a Microsoft Agent Framework
``Workflow`` graph instead, so it can be hosted as a Foundry hosted agent via
``agent-framework-foundry-hosting`` -- the supported long-term replacement for
portal Workflows.

The workflow graph is:

    dispatch-input --(fan-out)--> [architecture-agent, security-agent,
                                   cost-agent, resiliency-agent]
                    --(fan-in)--> aggregate-specialist-outputs
                                --> arb-summarizer-agent

Each specialist/summarizer node wraps the *same* persistent Foundry agents
already deployed by ``scripts/deploy_foundry_agents.py`` (looked up by name
via ``FoundryAgent``), so agent instructions/behavior stay single-sourced in
``src/agent_prompts.py`` and the Azure portal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_framework import (
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    FunctionExecutor,
    Message,
    Workflow,
    WorkflowAgent,
    WorkflowBuilder,
)
from agent_framework.foundry import FoundryAgent
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential

from src.agent_prompts import (
    ARB_CHAIRPERSON_AGENT_NAME,
    ARCHITECTURE_AGENT_NAME,
    COST_AGENT_NAME,
    RESILIENCY_AGENT_NAME,
    SECURITY_AGENT_NAME,
    build_chairperson_user_prompt,
    build_specialist_user_prompt,
)

SPECIALIST_AGENT_NAMES: list[str] = [
    ARCHITECTURE_AGENT_NAME,
    SECURITY_AGENT_NAME,
    COST_AGENT_NAME,
    RESILIENCY_AGENT_NAME,
]

# Maps a specialist agent name to the short key used in the summarizer prompt
# (see build_chairperson_user_prompt's specialist_outputs dict keys).
_SPECIALIST_OUTPUT_KEYS: dict[str, str] = {
    ARCHITECTURE_AGENT_NAME: "architecture",
    SECURITY_AGENT_NAME: "security",
    COST_AGENT_NAME: "cost",
    RESILIENCY_AGENT_NAME: "resiliency",
}


@dataclass
class ReviewInput:
    """Input payload for a single ARB review run."""

    page_title: str
    page_url: str
    page_text: str


def _project_endpoint() -> str:
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT") or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "AZURE_AI_PROJECT_ENDPOINT (or FOUNDRY_PROJECT_ENDPOINT) must be set to build the ARB workflow."
        )
    return endpoint


def build_arb_workflow(
    *,
    project_endpoint: str | None = None,
    credential: TokenCredential | None = None,
) -> Workflow:
    """Build the fan-out/fan-in ARB review Workflow.

    Mirrors ``scripts/orchestrate_persistent_agents.py``: the four specialist
    agents run in parallel against the same page content, then their combined
    output is fanned into ``arb-summarizer-agent`` for a consolidated
    markdown report.
    """

    endpoint = project_endpoint or _project_endpoint()
    cred = credential or DefaultAzureCredential()

    # Holds the ReviewInput for the in-flight run so the aggregator can
    # rebuild the summarizer prompt with the original page title/url.
    _run_state: dict[str, ReviewInput] = {}

    def _dispatch_input(messages: list[Message]) -> AgentExecutorRequest:
        """Fan-out entry point: turn the caller's raw input (the hosted-agent
        conversation input -- e.g. pasted design/page content) into the
        shared specialist prompt sent to all 4 specialist agents in
        parallel."""

        page_text = "\n".join(message.text for message in messages if message.text)
        review_input = ReviewInput(
            page_title="ARB Review Request",
            page_url="(submitted via hosted ARB review workflow)",
            page_text=page_text,
        )
        _run_state["current"] = review_input
        prompt = build_specialist_user_prompt(review_input.page_title, review_input.page_url, review_input.page_text)
        return AgentExecutorRequest(messages=[Message(role="user", contents=[prompt])])

    def _aggregate_specialist_outputs(responses: list[AgentExecutorResponse]) -> AgentExecutorRequest:
        """Fan-in point: combine the 4 specialist outputs into the
        summarizer's consolidation prompt, keyed by agent name."""

        review_input = _run_state["current"]
        outputs_by_key: dict[str, str] = {}
        for response in responses:
            key = _SPECIALIST_OUTPUT_KEYS.get(response.executor_id, response.executor_id)
            outputs_by_key[key] = response.agent_response.text

        prompt = build_chairperson_user_prompt(review_input.page_title, review_input.page_url, outputs_by_key)
        return AgentExecutorRequest(messages=[Message(role="user", contents=[prompt])])

    dispatcher = FunctionExecutor(_dispatch_input, id="dispatch-input")
    aggregator = FunctionExecutor(_aggregate_specialist_outputs, id="aggregate-specialist-outputs")

    specialist_executors = [
        AgentExecutor(
            FoundryAgent(project_endpoint=endpoint, agent_name=name, credential=cred),
            id=name,
        )
        for name in SPECIALIST_AGENT_NAMES
    ]

    summarizer_executor = AgentExecutor(
        FoundryAgent(project_endpoint=endpoint, agent_name=ARB_CHAIRPERSON_AGENT_NAME, credential=cred),
        id=ARB_CHAIRPERSON_AGENT_NAME,
    )

    builder = WorkflowBuilder(
        name="arb-review-workflow",
        description=(
            "Runs the ARB architecture/security/cost/resiliency specialist agents in parallel, "
            "then consolidates their reviews via the arb-summarizer-agent."
        ),
        start_executor=dispatcher,
        output_from=[summarizer_executor],
    )
    builder.add_fan_out_edges(dispatcher, specialist_executors)
    builder.add_fan_in_edges(specialist_executors, aggregator)
    builder.add_edge(aggregator, summarizer_executor)

    return builder.build()


def build_arb_workflow_agent(
    *,
    project_endpoint: str | None = None,
    credential: TokenCredential | None = None,
) -> WorkflowAgent:
    """Wrap the ARB review Workflow as an invokable Agent Framework agent
    suitable for hosting (e.g. via ``ResponsesHostServer``)."""

    workflow = build_arb_workflow(project_endpoint=project_endpoint, credential=credential)
    return WorkflowAgent(
        workflow,
        name="arb-review-workflow-agent",
        description="Hosted Agent Framework port of the ARB multi-agent review workflow.",
    )
