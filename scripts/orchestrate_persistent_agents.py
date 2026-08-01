"""Run ARB review by invoking deployed persistent Foundry agents."""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent_prompts import (  # noqa: E402
    ARB_CHAIRPERSON_AGENT_NAME,
    ARB_CHAIRPERSON_PROMPT,
    ARCHITECTURE_AGENT_NAME,
    COST_AGENT_NAME,
    RESILIENCY_AGENT_NAME,
    SECURITY_AGENT_NAME,
    SPECIALIST_AGENT_PROMPTS,
    build_chairperson_user_prompt,
    build_specialist_user_prompt,
)
from src.config import load_settings  # noqa: E402
from src.foundry_client import FoundryModelClient  # noqa: E402
from src.report_writer import write_review_markdown  # noqa: E402
from src.web_loader import fetch_public_page  # noqa: E402


def _extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output_items = getattr(response, "output", None) or []
    text_chunks: list[str] = []
    for item in output_items:
        if getattr(item, "type", None) != "message":
            continue
        for content_item in getattr(item, "content", None) or []:
            value = getattr(content_item, "text", None)
            if isinstance(value, str) and value.strip():
                text_chunks.append(value.strip())
    if text_chunks:
        return "\n".join(text_chunks).strip()

    raise RuntimeError("Persistent agent response did not include text output.")


def _invoke_persistent_agent(
    *,
    project_client: AIProjectClient,
    agent_name: str,
    user_prompt: str,
    max_output_tokens: int = 900,
) -> str:
    with project_client.get_openai_client(agent_name=agent_name) as openai_client:
        conversation = openai_client.conversations.create(
            items=[
                {
                    "type": "message",
                    "role": "user",
                    "content": user_prompt,
                }
            ]
        )
        try:
            payload: dict[str, Any] = {
                "conversation": conversation.id,
                "max_output_tokens": max_output_tokens,
                "reasoning": {"effort": "low"},
            }
            try:
                response = openai_client.responses.create(**payload)
            except Exception as exc:
                if not _is_unsupported_reasoning_for_agent_error(exc):
                    raise
                payload.pop("reasoning", None)
                response = openai_client.responses.create(**payload)
            response = _wait_for_response_completion(
                responses_client=openai_client.responses,
                response=response,
            )
            return _extract_response_text(response)
        finally:
            try:
                openai_client.conversations.delete(conversation_id=conversation.id)
            except Exception:
                pass


def _is_unsupported_reasoning_for_agent_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if "'reasoning'" not in message:
        return False
    return ("unsupported parameter" in message) or ("not allowed when agent is specified" in message)


def _wait_for_response_completion(*, responses_client: Any, response: Any, timeout_seconds: int = 120) -> Any:
    status = str(getattr(response, "status", "")).lower()
    if status in {"", "completed"}:
        return response

    response_id = getattr(response, "id", None)
    retrieve = getattr(responses_client, "retrieve", None)
    if not response_id or not callable(retrieve):
        return response

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = retrieve(response_id)
        status = str(getattr(response, "status", "")).lower()
        if status in {"completed", "failed", "incomplete", "cancelled"}:
            return response
        time.sleep(2)
    return response


def main() -> int:
    settings = load_settings()
    page = fetch_public_page(settings.blog_url, max_input_chars=settings.max_input_chars)
    specialist_input = build_specialist_user_prompt(page.title, page.url, page.text)

    fallback_model_client = FoundryModelClient(settings)
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    with AIProjectClient(endpoint=settings.azure_ai_foundry_project_endpoint, credential=credential) as project_client:
        def run_specialist(agent_name: str) -> str:
            try:
                return _invoke_persistent_agent(
                    project_client=project_client,
                    agent_name=agent_name,
                    user_prompt=specialist_input,
                )
            except RuntimeError as exc:
                # Some Foundry agent runs return incomplete/no text; fallback keeps orchestration reliable.
                print(f"[persistent-orchestration] Warning: {agent_name} fallback to model deployment: {exc}")
                return fallback_model_client.invoke_model(
                    system_prompt=SPECIALIST_AGENT_PROMPTS[agent_name],
                    user_prompt=specialist_input,
                    max_tokens=1200,
                )

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                "architecture": pool.submit(run_specialist, ARCHITECTURE_AGENT_NAME),
                "security": pool.submit(run_specialist, SECURITY_AGENT_NAME),
                "cost": pool.submit(run_specialist, COST_AGENT_NAME),
                "resiliency": pool.submit(run_specialist, RESILIENCY_AGENT_NAME),
            }
            specialist_outputs = {name: future.result() for name, future in futures.items()}

        chairperson_input = build_chairperson_user_prompt(
            page_title=page.title,
            page_url=page.url,
            specialist_outputs=specialist_outputs,
        )
        try:
            final_report = _invoke_persistent_agent(
                project_client=project_client,
                agent_name=ARB_CHAIRPERSON_AGENT_NAME,
                user_prompt=chairperson_input,
                max_output_tokens=1800,
            )
        except RuntimeError as exc:
            print(f"[persistent-orchestration] Warning: summarizer fallback to model deployment: {exc}")
            final_report = fallback_model_client.invoke_model(
                system_prompt=ARB_CHAIRPERSON_PROMPT,
                user_prompt=chairperson_input,
                max_tokens=1800,
            )

    result = {
        "page": {"title": page.title, "url": page.url},
        "specialist_reviews": specialist_outputs,
        "final_report_markdown": final_report,
    }
    output = write_review_markdown(settings.output_folder, result)
    print(f"[persistent-orchestration] Success: report generated at {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
