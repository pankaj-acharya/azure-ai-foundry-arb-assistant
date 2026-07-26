"""Configuration loading and validation for the ARB assistant."""

from __future__ import annotations

import os
from typing import Any

from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

try:
    from azure.keyvault.secrets import SecretClient
except ImportError:  # pragma: no cover
    SecretClient = None


class Settings(BaseModel):
    """Typed settings loaded from environment variables."""

    azure_subscription_id: str = ""
    azure_tenant_id: str = ""
    azure_resource_group: str = ""
    azure_ai_foundry_project_endpoint: str = Field(default="")
    azure_ai_foundry_project_name: str = ""
    azure_ai_foundry_model_deployment_name: str = Field(default="")
    blog_url: str = Field(default="")
    max_input_chars: int = Field(default=12000, ge=1000, le=100000)
    output_folder: str = "outputs"
    log_level: str = "INFO"

    @field_validator("azure_ai_foundry_project_endpoint")
    @classmethod
    def endpoint_must_look_like_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is required.")
        if not value.startswith("https://"):
            raise ValueError("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT must start with https://")
        return value

    @field_validator("azure_ai_foundry_model_deployment_name")
    @classmethod
    def deployment_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT_NAME is required.")
        return value

    @field_validator("blog_url")
    @classmethod
    def blog_url_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("BLOG_URL is required.")
        return value


def _load_key_vault_settings() -> dict[str, str]:
    """Load settings from Azure Key Vault when configured.

    Expected env vars:
    - AZURE_KEY_VAULT_URL (optional; enables Key Vault lookup when set)
    - Optional secret-name overrides:
      KV_SECRET_AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
      KV_SECRET_AZURE_AI_FOUNDRY_PROJECT_NAME
      KV_SECRET_AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT_NAME
      KV_SECRET_BLOG_URL
      KV_SECRET_AZURE_SUBSCRIPTION_ID
      KV_SECRET_AZURE_TENANT_ID
      KV_SECRET_AZURE_RESOURCE_GROUP
      KV_SECRET_MAX_INPUT_CHARS
      KV_SECRET_OUTPUT_FOLDER
      KV_SECRET_LOG_LEVEL
    """

    vault_url = os.getenv("AZURE_KEY_VAULT_URL", "").strip()
    if not vault_url:
        return {}

    if SecretClient is None:
        return {}

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    client = SecretClient(vault_url=vault_url, credential=credential)

    secret_names = {
        "azure_subscription_id": os.getenv("KV_SECRET_AZURE_SUBSCRIPTION_ID", "AZURE-SUBSCRIPTION-ID"),
        "azure_tenant_id": os.getenv("KV_SECRET_AZURE_TENANT_ID", "AZURE-TENANT-ID"),
        "azure_resource_group": os.getenv("KV_SECRET_AZURE_RESOURCE_GROUP", "AZURE-RESOURCE-GROUP"),
        "azure_ai_foundry_project_endpoint": os.getenv(
            "KV_SECRET_AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
            "AZURE-AI-FOUNDRY-PROJECT-ENDPOINT",
        ),
        "azure_ai_foundry_project_name": os.getenv(
            "KV_SECRET_AZURE_AI_FOUNDRY_PROJECT_NAME",
            "AZURE-AI-FOUNDRY-PROJECT-NAME",
        ),
        "azure_ai_foundry_model_deployment_name": os.getenv(
            "KV_SECRET_AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT_NAME",
            "AZURE-AI-FOUNDRY-MODEL-DEPLOYMENT-NAME",
        ),
        "blog_url": os.getenv("KV_SECRET_BLOG_URL", "BLOG-URL"),
        "max_input_chars": os.getenv("KV_SECRET_MAX_INPUT_CHARS", "MAX-INPUT-CHARS"),
        "output_folder": os.getenv("KV_SECRET_OUTPUT_FOLDER", "OUTPUT-FOLDER"),
        "log_level": os.getenv("KV_SECRET_LOG_LEVEL", "LOG-LEVEL"),
    }

    loaded: dict[str, str] = {}
    for setting_key, secret_name in secret_names.items():
        if not secret_name.strip():
            continue
        try:
            secret = client.get_secret(secret_name)
            if secret and secret.value is not None:
                loaded[setting_key] = secret.value
        except Exception:
            # Missing or inaccessible secrets should not break env fallback.
            continue
    return loaded


def _to_int(value: Any, default: int) -> int:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return int(text)


def load_settings() -> Settings:
    """Load settings from Key Vault first, then fallback to env values."""

    load_dotenv()

    env_values = {
        "azure_subscription_id": os.getenv("AZURE_SUBSCRIPTION_ID", ""),
        "azure_tenant_id": os.getenv("AZURE_TENANT_ID", ""),
        "azure_resource_group": os.getenv("AZURE_RESOURCE_GROUP", ""),
        "azure_ai_foundry_project_endpoint": os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", ""),
        "azure_ai_foundry_project_name": os.getenv("AZURE_AI_FOUNDRY_PROJECT_NAME", ""),
        "azure_ai_foundry_model_deployment_name": os.getenv("AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT_NAME", ""),
        "blog_url": os.getenv("BLOG_URL", ""),
        "max_input_chars": os.getenv("MAX_INPUT_CHARS", "12000"),
        "output_folder": os.getenv("OUTPUT_FOLDER", "outputs"),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
    }

    key_vault_values = _load_key_vault_settings()
    merged_values = {**env_values, **key_vault_values}

    try:
        return Settings(
            azure_subscription_id=str(merged_values.get("azure_subscription_id", "")),
            azure_tenant_id=str(merged_values.get("azure_tenant_id", "")),
            azure_resource_group=str(merged_values.get("azure_resource_group", "")),
            azure_ai_foundry_project_endpoint=str(merged_values.get("azure_ai_foundry_project_endpoint", "")),
            azure_ai_foundry_project_name=str(merged_values.get("azure_ai_foundry_project_name", "")),
            azure_ai_foundry_model_deployment_name=str(
                merged_values.get("azure_ai_foundry_model_deployment_name", "")
            ),
            blog_url=str(merged_values.get("blog_url", "")),
            max_input_chars=_to_int(merged_values.get("max_input_chars"), 12000),
            output_folder=str(merged_values.get("output_folder", "outputs")),
            log_level=str(merged_values.get("log_level", "INFO")),
        )
    except (ValidationError, ValueError) as exc:
        raise ValueError(f"Invalid configuration: {exc}") from exc
