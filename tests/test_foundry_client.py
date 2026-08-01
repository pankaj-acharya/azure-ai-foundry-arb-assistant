from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.foundry_client as foundry_client_module
from src.foundry_client import FoundryModelClient


class _FakeSettings:
    azure_ai_foundry_project_endpoint = "https://example.services.ai.azure.com/api/projects/demo"
    azure_ai_foundry_model_deployment_name = "gpt-4.1-mini"


class _FakeOpenAIResponsesClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text="  generated response  ")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeOpenAIResponsesClientTempUnsupported:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if "temperature" in kwargs:
            raise RuntimeError("Unsupported parameter: 'temperature' is not supported with this model.")
        return SimpleNamespace(output_text="fallback response")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_invoke_model_uses_get_openai_client_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    openai_client = _FakeOpenAIResponsesClient()

    class _FakeProjectClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def get_openai_client(self):
            return openai_client

    monkeypatch.setattr(foundry_client_module, "DefaultAzureCredential", lambda **kwargs: object())
    monkeypatch.setattr(foundry_client_module, "AIProjectClient", _FakeProjectClient)

    client = FoundryModelClient(_FakeSettings())
    result = client.invoke_model("system prompt", "user prompt", temperature=0.1, max_tokens=128)

    assert result == "generated response"
    assert openai_client.calls
    assert openai_client.calls[0]["model"] == "gpt-4.1-mini"
    assert openai_client.calls[0]["max_output_tokens"] == 128
    assert openai_client.calls[0]["instructions"] == "system prompt"
    assert openai_client.calls[0]["input"] == "user prompt"
    assert openai_client.calls[0]["reasoning"] == {"effort": "low"}


def test_invoke_model_retries_without_temperature_when_model_rejects_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_client = _FakeOpenAIResponsesClientTempUnsupported()

    class _FakeProjectClient:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

        def get_openai_client(self):
            return openai_client

    monkeypatch.setattr(foundry_client_module, "DefaultAzureCredential", lambda **kwargs: object())
    monkeypatch.setattr(foundry_client_module, "AIProjectClient", _FakeProjectClient)

    client = FoundryModelClient(_FakeSettings())
    result = client.invoke_model("system prompt", "user prompt", temperature=0.9, max_tokens=64)

    assert result == "fallback response"
    assert len(openai_client.calls) == 2
    assert "temperature" in openai_client.calls[0]
    assert "temperature" not in openai_client.calls[1]


def test_invoke_model_raises_clear_error_when_no_supported_apis(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProjectClientNoApis:
        def __init__(self, endpoint: str, credential: object):
            self.endpoint = endpoint
            self.credential = credential

    monkeypatch.setattr(foundry_client_module, "DefaultAzureCredential", lambda **kwargs: object())
    monkeypatch.setattr(foundry_client_module, "AIProjectClient", _FakeProjectClientNoApis)

    client = FoundryModelClient(_FakeSettings())
    with pytest.raises(RuntimeError, match="does not expose supported model invocation APIs"):
        client.invoke_model("system prompt", "user prompt")


def test_extract_response_text_supports_responses_output_message_shape() -> None:
    content_item = SimpleNamespace(text="hello from responses output")
    message_item = SimpleNamespace(type="message", content=[content_item])
    response = SimpleNamespace(output=[message_item], choices=None)
    assert FoundryModelClient._extract_response_text(response) == "hello from responses output"
