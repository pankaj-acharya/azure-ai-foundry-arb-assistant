"""Blocking one-agent smoke test for CI deployment validation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent_prompts import ARCHITECTURE_AGENT_PROMPT  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.foundry_client import FoundryModelClient  # noqa: E402
from src.web_loader import fetch_public_page  # noqa: E402


def main() -> int:
    settings = load_settings()

    print("[smoke-single] Loading public page content...")
    page = fetch_public_page(
        settings.blog_url,
        max_input_chars=min(settings.max_input_chars, 3000),
    )

    user_prompt = (
        "Review this public page content quickly for architecture quality.\n\n"
        f"Title: {page.title}\n"
        f"URL: {page.url}\n\n"
        "Extracted Content:\n"
        f"{page.text}"
    )

    print("[smoke-single] Invoking one Architecture Agent model call...")
    client = FoundryModelClient(settings)
    response = client.invoke_model(
        system_prompt=ARCHITECTURE_AGENT_PROMPT,
        user_prompt=user_prompt,
        temperature=0,
        max_tokens=350,
    )

    if not response.strip():
        print("[smoke-single] Failed: model returned empty response.")
        return 1

    print("[smoke-single] Success: model invocation returned non-empty output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
