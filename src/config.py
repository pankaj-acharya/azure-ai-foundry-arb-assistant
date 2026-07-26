"""Configuration loading and validation for the ARB assistant."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator


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


def load_settings() -> Settings:
    """Load settings from `.env` and process environment variables."""

    load_dotenv()
    try:
        return Settings(
            azure_subscription_id=os.getenv("AZURE_SUBSCRIPTION_ID", ""),
            azure_tenant_id=os.getenv("AZURE_TENANT_ID", ""),
            azure_resource_group=os.getenv("AZURE_RESOURCE_GROUP", ""),
            azure_ai_foundry_project_endpoint=os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", ""),
            azure_ai_foundry_project_name=os.getenv("AZURE_AI_FOUNDRY_PROJECT_NAME", ""),
            azure_ai_foundry_model_deployment_name=os.getenv("AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT_NAME", ""),
            blog_url=os.getenv("BLOG_URL", ""),
            max_input_chars=int(os.getenv("MAX_INPUT_CHARS", "12000")),
            output_folder=os.getenv("OUTPUT_FOLDER", "outputs"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
    except ValidationError as exc:
        raise ValueError(f"Invalid configuration: {exc}") from exc
