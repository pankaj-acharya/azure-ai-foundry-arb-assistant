"""Safe web-page loading and text extraction utilities."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


class WebLoaderError(RuntimeError):
    """Raised when web content cannot be safely loaded."""


@dataclass
class WebPageContent:
    """Normalized web-page content used by the orchestration layer."""

    title: str
    url: str
    text: str


def _is_ip_private(ip_text: str) -> bool:
    ip = ipaddress.ip_address(ip_text)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host_ips(hostname: str) -> Iterable[str]:
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return []
    return [item[4][0] for item in addr_info if item and item[4]]


def validate_public_url(url: str) -> None:
    """Validate URL scheme and reject obviously unsafe/internal targets."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed.")
    if not parsed.hostname:
        raise ValueError("URL must include a valid hostname.")

    hostname = parsed.hostname.strip().lower()
    blocked_hosts = {"localhost", "127.0.0.1", "::1"}
    if hostname in blocked_hosts or hostname.endswith(".local"):
        raise ValueError("Private/internal hosts are not allowed.")

    try:
        parsed_ip = ipaddress.ip_address(hostname)
        if _is_ip_private(str(parsed_ip)):
            raise ValueError("Private/internal IP addresses are not allowed.")
    except ValueError:
        # Hostname is not a literal IP, so resolve DNS and inspect records.
        pass

    for ip in _resolve_host_ips(hostname):
        if _is_ip_private(ip):
            raise ValueError("Resolved host points to a private/internal IP.")


def fetch_public_page(url: str, max_input_chars: int, timeout_seconds: int = 20) -> WebPageContent:
    """Fetch and sanitize a public page for prompt-friendly review input."""

    validate_public_url(url)

    try:
        response = requests.get(
            url,
            timeout=timeout_seconds,
            allow_redirects=True,
            headers={"User-Agent": "architecture-review-board-assistant/0.1"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise WebLoaderError(f"Unable to reach URL '{url}': {exc}") from exc

    # Re-validate the final destination after redirects.
    validate_public_url(response.url)

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "form", "aside"]):
        tag.decompose()

    title = (soup.title.string or "Untitled page").strip() if soup.title else "Untitled page"
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        raise WebLoaderError("The page was reachable but no readable text could be extracted.")

    trimmed = text[:max_input_chars]
    return WebPageContent(title=title, url=response.url, text=trimmed)
