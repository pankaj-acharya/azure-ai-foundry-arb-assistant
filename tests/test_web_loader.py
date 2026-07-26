"""Unit tests for safe URL validation and extraction behavior."""

from __future__ import annotations

import pytest

from src.web_loader import fetch_public_page, validate_public_url


def test_validate_public_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="Only http/https"):
        validate_public_url("ftp://example.com/file.txt")


def test_validate_public_url_rejects_localhost() -> None:
    with pytest.raises(ValueError, match="Private/internal hosts"):
        validate_public_url("http://localhost:8000")


def test_fetch_public_page_extracts_text_and_trims(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self):
            self.url = "https://example.com/post"
            self.text = """
            <html>
              <head><title>Example Post</title><style>.x{display:none;}</style></head>
              <body>
                <nav>Ignore me</nav>
                <main>
                  <h1>Hello</h1>
                  <p>This is useful architecture content.</p>
                </main>
                <script>console.log('noise')</script>
              </body>
            </html>
            """

        def raise_for_status(self):
            return None

    def fake_get(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("src.web_loader.requests.get", fake_get)
    page = fetch_public_page("https://example.com/post", max_input_chars=25)
    assert page.title == "Example Post"
    assert page.url == "https://example.com/post"
    assert "noise" not in page.text.lower()
    assert len(page.text) <= 25
