"""Simple parallel multi-agent orchestration for ARB reviews."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .agent_prompts import (
    ARB_CHAIRPERSON_PROMPT,
    ARCHITECTURE_AGENT_PROMPT,
    COST_AGENT_PROMPT,
    RESILIENCY_AGENT_PROMPT,
    SECURITY_AGENT_PROMPT,
    build_chairperson_user_prompt,
    build_specialist_user_prompt,
)
from .web_loader import WebPageContent


class ARBOrchestrator:
    """Coordinates specialist agents and a summarizer agent."""

    def __init__(self, model_client):
        self.model_client = model_client

    def run_review(self, page: WebPageContent) -> dict:
        """Run specialist reviews in parallel and consolidate them with the summarizer agent."""

        specialist_input = build_specialist_user_prompt(page.title, page.url, page.text)

        def run_architecture() -> str:
            return self.model_client.invoke_model(ARCHITECTURE_AGENT_PROMPT, specialist_input)

        def run_security() -> str:
            return self.model_client.invoke_model(SECURITY_AGENT_PROMPT, specialist_input)

        def run_cost() -> str:
            return self.model_client.invoke_model(COST_AGENT_PROMPT, specialist_input)

        def run_resiliency() -> str:
            return self.model_client.invoke_model(RESILIENCY_AGENT_PROMPT, specialist_input)

        # The specialists are independent, so we execute them in parallel for clarity and speed.
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                "architecture": pool.submit(run_architecture),
                "security": pool.submit(run_security),
                "cost": pool.submit(run_cost),
                "resiliency": pool.submit(run_resiliency),
            }
            specialist_outputs = {name: future.result() for name, future in futures.items()}

        chairperson_user_prompt = build_chairperson_user_prompt(
            page_title=page.title,
            page_url=page.url,
            specialist_outputs=specialist_outputs,
        )

        final_markdown = self.model_client.invoke_model(ARB_CHAIRPERSON_PROMPT, chairperson_user_prompt)

        return {
            "page": {"title": page.title, "url": page.url},
            "specialist_reviews": specialist_outputs,
            "final_report_markdown": final_markdown,
        }
