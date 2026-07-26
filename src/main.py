"""CLI entry point for the ARB assistant."""

from __future__ import annotations

import logging

from rich.console import Console

from .config import load_settings
from .foundry_client import FoundryModelClient
from .orchestrator import ARBOrchestrator
from .report_writer import write_review_markdown
from .web_loader import fetch_public_page


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> None:
    """Run end-to-end ARB review flow against a public page."""

    console = Console()
    settings = load_settings()
    configure_logging(settings.log_level)

    console.print("[bold cyan]Loading public page content...[/bold cyan]")
    page = fetch_public_page(
        url=settings.blog_url,
        max_input_chars=settings.max_input_chars,
    )

    console.print("[bold cyan]Invoking Azure AI Foundry specialist agents...[/bold cyan]")
    model_client = FoundryModelClient(settings)
    orchestrator = ARBOrchestrator(model_client)
    result = orchestrator.run_review(page)

    report_path = write_review_markdown(settings.output_folder, result)
    console.print(f"[bold green]Review complete.[/bold green] Report: {report_path}")


if __name__ == "__main__":
    main()
