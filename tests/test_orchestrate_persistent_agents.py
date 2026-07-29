from __future__ import annotations

from types import SimpleNamespace

from scripts import orchestrate_persistent_agents as orchestration_script


class _FakeConversationClient:
    def __init__(self) -> None:
        self.deleted_ids: list[str] = []

    def create(self, items):
        return SimpleNamespace(id="conv-123")

    def delete(self, *, conversation_id: str) -> None:
        self.deleted_ids.append(conversation_id)


class _FakeResponsesClientReasoningRejected:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "reasoning" in kwargs:
            raise RuntimeError(
                "Error code: 400 - {'error': {'code': 'invalid_payload', "
                "'message': 'Not allowed when agent is specified.', 'param': 'reasoning'}}"
            )
        return SimpleNamespace(output_text="agent response")


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.conversations = _FakeConversationClient()
        self.responses = _FakeResponsesClientReasoningRejected()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeProjectClient:
    def __init__(self) -> None:
        self.openai = _FakeOpenAIClient()

    def get_openai_client(self, *, agent_name: str):
        return self.openai


def test_invoke_persistent_agent_retries_without_reasoning_for_agent_calls() -> None:
    project_client = _FakeProjectClient()

    output = orchestration_script._invoke_persistent_agent(
        project_client=project_client,
        agent_name="architecture-agent",
        user_prompt="review input",
    )

    assert output == "agent response"
    assert len(project_client.openai.responses.calls) == 2
    assert "reasoning" in project_client.openai.responses.calls[0]
    assert "reasoning" not in project_client.openai.responses.calls[1]
    assert project_client.openai.conversations.deleted_ids == ["conv-123"]

