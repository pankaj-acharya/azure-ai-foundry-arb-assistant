"""Simple smoke test for the local orchestration path."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402
from src.foundry_client import FoundryModelClient  # noqa: E402
from src.orchestrator import ARBOrchestrator  # noqa: E402
from src.web_loader import fetch_public_page  # noqa: E402


def main() -> int:
    settings = load_settings()
    print("[smoke] Loading public page...")
    page = fetch_public_page(settings.blog_url, max_input_chars=min(settings.max_input_chars, 4000))

    print("[smoke] Invoking multi-agent orchestration...")
    client = FoundryModelClient(settings)
    orchestrator = ARBOrchestrator(client)
    result = orchestrator.run_review(page)

    report = result.get("final_report_markdown", "")
    if not report.strip():
        print("[smoke] Failed: final report is empty.")
        return 1

    print("[smoke] Success: final report generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
