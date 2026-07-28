"""Unit tests for scripts/deploy_foundry_agents.py helper functions."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# Ensure the repo root is on sys.path so the scripts/ package is importable.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.deploy_foundry_agents as deploy_module
from scripts.deploy_foundry_agents import (
    _build_version_definition,
    _create_base_agent_if_missing,
    _deploy_with_create_agent,
    _deploy_with_create_or_update_agent,
    _deploy_with_create_version,
    _is_not_found_error,
)


# ---------------------------------------------------------------------------
# _is_not_found_error
# ---------------------------------------------------------------------------


def test_is_not_found_error_status_code_attribute() -> None:
    exc = Exception("not found")
    exc.status_code = 404  # type: ignore[attr-defined]
    assert _is_not_found_error(exc) is True


def test_is_not_found_error_status_attribute() -> None:
    exc = Exception("not found")
    exc.status = 404  # type: ignore[attr-defined]
    assert _is_not_found_error(exc) is True


def test_is_not_found_error_message_contains_404() -> None:
    exc = ValueError("HTTP 404 resource not found")
    assert _is_not_found_error(exc) is True


def test_is_not_found_error_false_for_other_errors() -> None:
    exc = RuntimeError("500 internal server error")
    assert _is_not_found_error(exc) is False


# ---------------------------------------------------------------------------
# _build_version_definition
# ---------------------------------------------------------------------------


def test_build_version_definition_uses_prompt_agent_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakePromptAgentDefinition:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(deploy_module, "PromptAgentDefinition", FakePromptAgentDefinition)

    result = _build_version_definition("gpt-4", "Do the thing")

    assert isinstance(result, FakePromptAgentDefinition)
    assert calls == [{"model": "gpt-4", "instructions": "Do the thing"}]


def test_build_version_definition_falls_back_to_dict_when_class_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy_module, "PromptAgentDefinition", None)

    result = _build_version_definition("gpt-4", "Do the thing")

    assert result == {"model": "gpt-4", "instructions": "Do the thing"}


# ---------------------------------------------------------------------------
# _create_base_agent_if_missing
# ---------------------------------------------------------------------------


def test_create_base_agent_if_missing_uses_create_or_update_when_available() -> None:
    calls: list[Any] = []

    def fake_create_or_update(body: Any) -> None:
        calls.append(body)

    ok, msg = _create_base_agent_if_missing(
        name="my-agent",
        model_name="gpt-4",
        instructions="instructions",
        create_or_update=fake_create_or_update,
        create_agent=None,
    )

    assert ok is True
    assert "create_or_update_agent" in msg
    assert calls


def test_create_base_agent_if_missing_falls_back_to_create_agent() -> None:
    calls: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> None:
        calls.append(kwargs)

    ok, msg = _create_base_agent_if_missing(
        name="my-agent",
        model_name="gpt-4",
        instructions="instructions",
        create_or_update=None,
        create_agent=fake_create_agent,
    )

    assert ok is True
    assert "create_agent" in msg
    assert calls[0]["name"] == "my-agent"
    assert calls[0]["model"] == "gpt-4"


def test_create_base_agent_if_missing_returns_false_when_no_method() -> None:
    ok, msg = _create_base_agent_if_missing(
        name="my-agent",
        model_name="gpt-4",
        instructions="instructions",
        create_or_update=None,
        create_agent=None,
    )

    assert ok is False
    assert "No SDK method available" in msg


def test_create_base_agent_if_missing_handles_create_or_update_exception() -> None:
    def failing_create_or_update(body: Any) -> None:
        raise RuntimeError("network error")

    ok, msg = _create_base_agent_if_missing(
        name="my-agent",
        model_name="gpt-4",
        instructions="instructions",
        create_or_update=failing_create_or_update,
        create_agent=None,
    )

    assert ok is False
    assert "network error" in msg


# ---------------------------------------------------------------------------
# _deploy_with_create_or_update_agent
# ---------------------------------------------------------------------------


def test_deploy_with_create_or_update_agent_success() -> None:
    upserted: list[Any] = []

    def fake_create_or_update(body: Any) -> None:
        upserted.append(body)

    definitions = {"agent-a": "Do A", "agent-b": "Do B"}
    count, errors = _deploy_with_create_or_update_agent(fake_create_or_update, "gpt-4", definitions)

    assert count == 2
    assert errors == []
    assert len(upserted) == 2


def test_deploy_with_create_or_update_agent_records_errors_on_failure() -> None:
    def failing_upsert(body: Any) -> None:
        raise RuntimeError("upstream error")

    count, errors = _deploy_with_create_or_update_agent(failing_upsert, "gpt-4", {"agent-a": "Do A"})

    assert count == 0
    assert len(errors) == 1
    assert "upstream error" in errors[0]


# ---------------------------------------------------------------------------
# _deploy_with_create_agent
# ---------------------------------------------------------------------------


def test_deploy_with_create_agent_success() -> None:
    created: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> None:
        created.append(kwargs)

    definitions = {"agent-a": "Do A"}
    count, errors = _deploy_with_create_agent(fake_create_agent, "gpt-4", definitions)

    assert count == 1
    assert errors == []
    assert created[0]["name"] == "agent-a"
    assert created[0]["model"] == "gpt-4"
    assert created[0]["instructions"] == "Do A"


def test_deploy_with_create_agent_records_errors_on_failure() -> None:
    def failing_create(**kwargs: Any) -> None:
        raise RuntimeError("create failed")

    count, errors = _deploy_with_create_agent(failing_create, "gpt-4", {"agent-a": "Do A"})

    assert count == 0
    assert len(errors) == 1
    assert "create failed" in errors[0]


# ---------------------------------------------------------------------------
# _deploy_with_create_version
# ---------------------------------------------------------------------------


def test_deploy_with_create_version_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_module, "PromptAgentDefinition", None)

    versioned: list[dict[str, Any]] = []

    def fake_create_version(agent_name: str, definition: Any) -> None:
        versioned.append({"agent_name": agent_name, "definition": definition})

    definitions = {"agent-a": "Do A", "agent-b": "Do B"}
    count, errors = _deploy_with_create_version(
        fake_create_version, "gpt-4", definitions, None, None
    )

    assert count == 2
    assert errors == []
    assert versioned[0]["agent_name"] == "agent-a"


def test_deploy_with_create_version_recovers_404_via_create_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy_module, "PromptAgentDefinition", None)

    call_count = {"version": 0}

    def fake_create_version(agent_name: str, definition: Any) -> None:
        call_count["version"] += 1
        if call_count["version"] == 1:
            err = ResourceNotFoundError()
            err.status_code = 404  # type: ignore[attr-defined]
            raise err
        # Second call (after recovery) succeeds.

    created: list[dict[str, Any]] = []

    def fake_create_agent(**kwargs: Any) -> None:
        created.append(kwargs)

    count, errors = _deploy_with_create_version(
        fake_create_version, "gpt-4", {"agent-a": "Do A"}, None, fake_create_agent
    )

    assert count == 1
    assert errors == []
    assert created[0]["name"] == "agent-a"


class ResourceNotFoundError(Exception):
    """Minimal stand-in for azure.core.exceptions.ResourceNotFoundError."""


def test_deploy_with_create_version_fails_404_no_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy_module, "PromptAgentDefinition", None)

    def always_404(agent_name: str, definition: Any) -> None:
        err = ResourceNotFoundError()
        err.status_code = 404  # type: ignore[attr-defined]
        raise err

    count, errors = _deploy_with_create_version(
        always_404, "gpt-4", {"agent-a": "Do A"}, None, None
    )

    assert count == 0
    assert len(errors) == 1
    assert "No SDK method available" in errors[0]


def test_deploy_with_create_version_records_non_404_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deploy_module, "PromptAgentDefinition", None)

    def server_error(agent_name: str, definition: Any) -> None:
        raise RuntimeError("500 internal error")

    count, errors = _deploy_with_create_version(
        server_error, "gpt-4", {"agent-a": "Do A"}, None, None
    )

    assert count == 0
    assert len(errors) == 1
    assert "500 internal error" in errors[0]
