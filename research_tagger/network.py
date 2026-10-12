"""Bounded public HTTPS downloads for configurable conference and arXiv sources."""

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "ResearchTagger/0.1 (public paper title and abstract reader)"


def public_url(url: str) -> str:
    """Reject credentials, local addresses, and non-HTTPS URLs before fetching."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Sources must be public HTTPS URLs without credentials.")
    for record in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(record[4][0]).is_global:
            raise ValueError("Local and private network sources are not supported.")
    return url


def download(client: httpx.Client, url: str, limit: int = 16_000_000) -> bytes:
    """Fetch up to limit bytes, validating each redirect; raise on HTTP failures."""
    for _ in range(5):
        public_url(url)
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                continue
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > limit:
                    raise ValueError(f"Source exceeds the {limit:,}-byte download limit.")
            return bytes(content)
    raise ValueError("Source redirected too many times.")
