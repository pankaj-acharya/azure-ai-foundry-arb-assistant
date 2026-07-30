"""Unit tests for deploy helper behavior and diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

# Ensure the repo root is on sys.path so scripts/ is importable in all test runners.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import deploy_foundry_agents as deploy_script


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}


class _FakeErrorModel:
    class _Error:
        def __init__(self, code: str):
            self.code = code

    def __init__(self, code: str):
        self.error = self._Error(code)


class _FakeHttpError(Exception):
    def __init__(self, message: str, status_code: int, error_code: str):
        super().__init__(message)
        self.status_code = status_code
        self.response = _FakeResponse(status_code, {"x-ms-request-id": "req-123"})
        self.model = _FakeErrorModel(error_code)


class _FakeSettings:
    azure_ai_foundry_project_endpoint = "https://example.services.ai.azure.com/api/projects/demo"
    azure_ai_foundry_model_deployment_name = "gpt-4.1"


class _FakeAgentsApi:
    def __init__(self) -> None:
        self.list_called = False

    def list(self, limit: int = 1):
        self.list_called = True
        return []


class _FakeOpenAIClient:
    def __init__(self, *, raise_on_create: Exception | None = None) -> None:
        self._raise = raise_on_create
        self.create_calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @property
    def responses(self):
        outer = self

        class _Responses:
            def create(self_, **kwargs):
                outer.create_calls.append(kwargs)
                if outer._raise:
                    raise outer._raise
                return SimpleNamespace(output_text="ok")

        return _Responses()


class _FakeProjectClient:
    def __init__(self, *, raise_on_inference: Exception | None = None) -> None:
        self._raise = raise_on_inference
        self._openai_client = _FakeOpenAIClient(raise_on_create=raise_on_inference)

    def get_openai_client(self):
        return self._openai_client


def test_validate_project_endpoint_format_accepts_expected_shape() -> None:
    ok, message = deploy_script._validate_project_endpoint_format(
        "https://archreviewassistant.services.ai.azure.com/api/projects/my-foundry-project"
    )
    assert ok is True
    assert "valid" in message.lower()


def test_validate_project_endpoint_format_rejects_non_project_endpoint() -> None:
    ok, message = deploy_script._validate_project_endpoint_format(
        "https://archreviewassistant.openai.azure.com/"
    )
    assert ok is False
    assert "services.ai.azure.com" in message or "/api/projects/" in message


def test_format_exception_context_includes_status_code_service_code_and_request_id() -> None:
    exc = _FakeHttpError("permission denied", status_code=403, error_code="PermissionDenied")
    context = deploy_script._format_exception_context(exc)
    assert "http_status=403" in context
    assert "service_code=PermissionDenied" in context
    assert "request_id=req-123" in context


def test_is_permission_error_detects_permissiondenied_service_code() -> None:
    exc = _FakeHttpError("forbidden", status_code=403, error_code="PermissionDenied")
    assert deploy_script._is_permission_error(exc) is True


def test_preflight_skips_inference_probe_for_disable_action() -> None:
    """Preflight with action=disable must not call the inference probe."""
    openai_client = _FakeOpenAIClient()

    class _Client:
        def get_openai_client(self):
            return openai_client

    agents_api = _FakeAgentsApi()
    ok, errors = deploy_script._run_preflight_checks(
        settings=_FakeSettings(),
        client=_Client(),
        agents_api=agents_api,
        create_or_update=None,
        create_agent=None,
        create_version=lambda **_: None,
        delete_version=None,
        delete_agent=None,
        action="disable",
    )
    assert openai_client.create_calls == [], "Inference probe must not run for disable action"
    assert agents_api.list_called, "Agents list probe should still run"


def test_preflight_skips_inference_probe_for_delete_action() -> None:
    """Preflight with action=delete must not call the inference probe."""
    openai_client = _FakeOpenAIClient()

    class _Client:
        def get_openai_client(self):
            return openai_client

    agents_api = _FakeAgentsApi()
    deploy_script._run_preflight_checks(
        settings=_FakeSettings(),
        client=_Client(),
        agents_api=agents_api,
        create_or_update=None,
        create_agent=None,
        create_version=lambda **_: None,
        delete_version=None,
        delete_agent=None,
        action="delete",
    )
    assert openai_client.create_calls == [], "Inference probe must not run for delete action"


def test_inference_probe_failure_includes_model_name_and_hint() -> None:
    """DeploymentNotFound inference failure should name the model and explain how to fix it."""
    not_found_exc = _FakeHttpError("not found", status_code=404, error_code="DeploymentNotFound")

    class _Client:
        def get_openai_client(self):
            return _FakeOpenAIClient(raise_on_create=not_found_exc)

    ok, message = deploy_script._preflight_chat_probe(_Client(), "gpt-5.4")
    assert ok is False
    assert "gpt-5.4" in message, "Error message must include the model name tried"
    assert "deploy" in message.lower() or "foundry" in message.lower(), "Error must include remediation hint"


def test_preflight_skips_inference_probe_for_enable_action() -> None:
    """Preflight with action=enable must not call the inference probe."""
    openai_client = _FakeOpenAIClient()

    class _Client:
        def get_openai_client(self):
            return openai_client

    agents_api = _FakeAgentsApi()
    deploy_script._run_preflight_checks(
        settings=_FakeSettings(),
        client=_Client(),
        agents_api=agents_api,
        create_or_update=None,
        create_agent=None,
        create_version=lambda **_: None,
        delete_version=None,
        delete_agent=None,
        action="enable",
    )
    assert openai_client.create_calls == [], "Inference probe must not run for enable action"


def test_enable_all_agents_calls_enable_method() -> None:
    """_enable_all_agents should call agents_api.enable() when available."""
    enabled_names: list[str] = []

    class _AgentsApiWithEnable:
        def enable(self, *, agent_name: str) -> None:
            enabled_names.append(agent_name)

    agent_names = ["architecture-agent", "security-agent"]
    count, errors = deploy_script._enable_all_agents(
        agents_api=_AgentsApiWithEnable(),
        agent_names=agent_names,
    )
    assert count == 2
    assert errors == []
    assert enabled_names == agent_names


def test_enable_all_agents_fallback_to_update_when_no_enable_method() -> None:
    """_enable_all_agents falls back to update(enabled=True) when enable() not present."""
    update_calls: list[dict] = []

    class _AgentsApiWithUpdate:
        def update(self, *, agent_name: str, enabled: bool) -> None:
            update_calls.append({"agent_name": agent_name, "enabled": enabled})

    agent_names = ["cost-agent"]
    count, errors = deploy_script._enable_all_agents(
        agents_api=_AgentsApiWithUpdate(),
        agent_names=agent_names,
    )
    assert count == 1
    assert update_calls == [{"agent_name": "cost-agent", "enabled": True}]


def test_enable_all_agents_portal_fallback_when_no_sdk_method() -> None:
    """_enable_all_agents appends fallback message when no SDK method available."""

    class _AgentsApiNoEnableOrUpdate:
        pass

    count, errors = deploy_script._enable_all_agents(
        agents_api=_AgentsApiNoEnableOrUpdate(),
        agent_names=["resiliency-agent"],
    )
    assert count == 0
    assert len(errors) == 1
    assert "portal" in errors[0].lower() or "foundry" in errors[0].lower()
