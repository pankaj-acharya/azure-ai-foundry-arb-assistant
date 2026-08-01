"""Tests for the Agent Framework hosted-workflow port of the ARB orchestration.

These tests build the workflow graph against a mocked credential (no real
Foundry project is contacted) and assert the graph shape: 1 dispatcher -> 4
parallel specialists -> 1 aggregator -> 1 summarizer.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from src.agent_framework_workflow import (
    ARB_CHAIRPERSON_AGENT_NAME,
    SPECIALIST_AGENT_NAMES,
    build_arb_workflow,
    build_arb_workflow_agent,
)

FAKE_ENDPOINT = "https://example-project.services.ai.azure.com/api/projects/example"


@pytest.fixture(autouse=True)
def _clear_endpoint_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)


def test_build_arb_workflow_requires_project_endpoint() -> None:
    with pytest.raises(RuntimeError, match="AZURE_AI_PROJECT_ENDPOINT"):
        build_arb_workflow(credential=MagicMock())


def test_build_arb_workflow_reads_endpoint_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", FAKE_ENDPOINT)
    workflow = build_arb_workflow(credential=MagicMock())
    assert workflow.name == "arb-review-workflow"


def test_build_arb_workflow_uses_explicit_endpoint_arg() -> None:
    workflow = build_arb_workflow(project_endpoint=FAKE_ENDPOINT, credential=MagicMock())
    assert workflow.name == "arb-review-workflow"


def test_build_arb_workflow_includes_all_specialists_and_summarizer() -> None:
    workflow = build_arb_workflow(project_endpoint=FAKE_ENDPOINT, credential=MagicMock())
    executor_ids = {executor.id for executor in workflow.get_executors_list()}
    for name in [*SPECIALIST_AGENT_NAMES, ARB_CHAIRPERSON_AGENT_NAME, "dispatch-input", "aggregate-specialist-outputs"]:
        assert name in executor_ids


def test_build_arb_workflow_agent_wraps_workflow() -> None:
    agent = build_arb_workflow_agent(project_endpoint=FAKE_ENDPOINT, credential=MagicMock())
    assert agent.name == "arb-review-workflow-agent"
