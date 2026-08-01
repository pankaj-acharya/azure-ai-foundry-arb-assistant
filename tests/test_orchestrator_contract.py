"""Contract tests for orchestrator structure and specialist/chair flow."""

from __future__ import annotations

from src.orchestrator import ARBOrchestrator
from src.web_loader import WebPageContent


class FakeModelClient:
    def invoke_model(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        prompt_l = system_prompt.lower()
        if "arb summarizer" in prompt_l:
            return (
                "## Executive summary\n"
                "Good learning architecture.\n\n"
                "## Strengths\n- Clear scope\n\n"
                "## Key risks\n- Missing retries\n\n"
                "## Recommended improvements\n- Add timeout handling\n\n"
                "## Decision\nApproved with conditions\n\n"
                "## Learning notes for a first-time Azure AI Foundry learner\n"
                "Start small and iterate."
            )
        if "architecture agent" in prompt_l:
            return "Architecture strengths and risks."
        if "security agent" in prompt_l:
            return "Security strengths and risks."
        if "cost agent" in prompt_l:
            return "Cost strengths and risks."
        if "resiliency agent" in prompt_l:
            return "Resiliency strengths and risks."
        return "Unexpected prompt type"


def test_orchestrator_returns_expected_contract() -> None:
    orchestrator = ARBOrchestrator(FakeModelClient())
    page = WebPageContent(
        title="My Blog Post",
        url="https://example.com/blog",
        text="This is a sample cloud architecture discussion.",
    )

    result = orchestrator.run_review(page)
    assert "page" in result
    assert "specialist_reviews" in result
    assert "final_report_markdown" in result

    specialists = result["specialist_reviews"]
    assert set(specialists.keys()) == {"architecture", "security", "cost", "resiliency"}
    assert "Decision" in result["final_report_markdown"]
