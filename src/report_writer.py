"""Utilities to write ARB review outputs to markdown files."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def write_review_markdown(output_folder: str, result: dict) -> Path:
    """Write the final ARB report to a deterministic markdown path."""

    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "arb-review.md"

    generated_at = datetime.now(timezone.utc).isoformat()
    content = (
        f"# Architecture Review Board Report\n\n"
        f"Generated at (UTC): {generated_at}\n\n"
        f"Reviewed page: {result['page']['title']}\n"
        f"Source URL: {result['page']['url']}\n\n"
        f"{result['final_report_markdown']}\n"
    )

    target.write_text(content, encoding="utf-8")
    return target
