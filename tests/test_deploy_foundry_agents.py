"""Unit tests for deploy helper behavior and diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

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
