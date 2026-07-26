"""Azure AI Foundry model client wrapper."""

from __future__ import annotations

from typing import Any

from azure.identity import DefaultAzureCredential

try:
    from azure.ai.projects import AIProjectClient
except ImportError:  # pragma: no cover
    AIProjectClient = None

from .config import Settings


class FoundryModelClient:
    """Thin wrapper around Azure AI Foundry model invocation."""

    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.azure_ai_foundry_project_endpoint:
            raise ValueError("Missing AZURE_AI_FOUNDRY_PROJECT_ENDPOINT.")
        if not settings.azure_ai_foundry_model_deployment_name:
            raise ValueError("Missing AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT_NAME.")
        if AIProjectClient is None:
            raise RuntimeError(
                "azure-ai-projects is not available. Install dependencies with 'pip install -r requirements.txt'."
            )

        self.credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
        try:
            self.client = AIProjectClient(
                endpoint=settings.azure_ai_foundry_project_endpoint,
                credential=self.credential,
            )
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Failed to connect to Azure AI Foundry project endpoint. "
                "Check AZURE_AI_FOUNDRY_PROJECT_ENDPOINT and Azure login status."
            ) from exc

    def invoke_model(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 900,
    ) -> str:
        """Invoke the configured model deployment with defensive SDK compatibility handling."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            return self._invoke_with_known_sdk_shapes(messages, temperature, max_tokens)
        except Exception as exc:
            raise RuntimeError(
                "Model invocation failed. Ensure Azure authentication is available (az login), "
                "project endpoint is correct, and deployment name exists in the Foundry project."
            ) from exc

    def _invoke_with_known_sdk_shapes(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
        deployment = self.settings.azure_ai_foundry_model_deployment_name
        inference = getattr(self.client, "inference", None)
        if inference is None:
            raise RuntimeError("Current azure-ai-projects SDK does not expose inference APIs on AIProjectClient.")

        # Shape 1: inference.get_chat_completions(...)
        if hasattr(inference, "get_chat_completions"):
            response = inference.get_chat_completions(
                model=deployment,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return self._extract_response_text(response)

        # Shape 2: inference.chat_completions.create(...)
        chat_completions = getattr(inference, "chat_completions", None)
        if chat_completions is not None and hasattr(chat_completions, "create"):
            response = chat_completions.create(
                model=deployment,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return self._extract_response_text(response)

        raise RuntimeError(
            "No supported chat-completions invocation method found in current azure-ai-projects SDK."
        )

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            raise RuntimeError("Foundry model response had no choices.")

        first = choices[0]
        message = getattr(first, "message", None)
        if message is not None:
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                return "\n".join(str(part) for part in content).strip()

        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text.strip()

        raise RuntimeError("Unable to parse text from Foundry model response.")
