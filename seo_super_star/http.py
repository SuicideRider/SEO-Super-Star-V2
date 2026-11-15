"""Page fetching with manual redirect handling and a private-network guard."""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 SEOSuperStar/0.1"
)
MAX_REDIRECTS = 10
MAX_BODY_BYTES = 5 * 1024 * 1024


@dataclass
class FetchResult:
    requested_url: str
    final_url: str = ""
    status_code: int | None = None
    response_ms: int | None = None
    total_ms: int | None = None
    redirect_chain: list[dict] = field(default_factory=list)
    content_type: str = ""
    x_robots_tag: str = ""
    body: bytes = b""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.status_code is not None

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type.lower() or (
            not self.content_type and self.body.lstrip()[:1] == b"<"
        )

    def summary(self) -> dict:
        return {
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "status_code": self.status_code,
            "response_ms": self.response_ms,
            "total_ms": self.total_ms,
            "redirect_chain": self.redirect_chain,
            "content_type": self.content_type,
            "x_robots_tag": self.x_robots_tag,
            "bytes": len(self.body),
            "error": self.error,
        }


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return url


def check_public_url(url: str) -> str:
    """Return an error message if the URL must not be fetched, else ''."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "Only http and https URLs can be audited."
    if not parsed.hostname:
        return "The URL has no hostname."
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return f"Could not resolve host {parsed.hostname}."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return "Private and local network addresses cannot be audited."
    return ""


def fetch_page(url: str, timeout: int = 15, allow_private: bool = False,
               session: requests.Session | None = None,
               read_body: bool = True) -> FetchResult:
    """Fetch a URL, following redirects one hop at a time.

    read_body=False returns the status and headers only (used for link checks).

    Each hop is checked against the private-network guard, so a public URL
    cannot redirect the auditor into an internal address.
    """
    url = normalize_url(url)
    result = FetchResult(requested_url=url, final_url=url)
    if not url:
        result.error = "No URL given."
        return result

    http = session or requests.Session()
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"}
    started = time.perf_counter()
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not allow_private:
                problem = check_public_url(current)
                if problem:
                    result.error = problem
                    return result
            hop_started = time.perf_counter()
            resp = http.get(current, headers=headers, timeout=timeout,
                            allow_redirects=False, stream=True)
            hop_ms = int((time.perf_counter() - hop_started) * 1000)
            location = resp.headers.get("Location")
            if resp.is_redirect and location:
                result.redirect_chain.append(
                    {"url": current, "status_code": resp.status_code})
                resp.close()
                current = urljoin(current, location)
                continue

            body = b""
            if read_body:
                for chunk in resp.iter_content(chunk_size=65536):
                    body += chunk
                    if len(body) >= MAX_BODY_BYTES:
                        break
            resp.close()
            result.final_url = current
            result.status_code = resp.status_code
            result.response_ms = hop_ms
            result.content_type = resp.headers.get("Content-Type", "")
            result.x_robots_tag = resp.headers.get("X-Robots-Tag", "")
            result.body = body
            break
        else:
            result.error = f"Stopped after {MAX_REDIRECTS} redirects."
    except requests.exceptions.Timeout:
        result.error = f"Timed out after {timeout} seconds."
    except requests.exceptions.SSLError as exc:
        result.error = f"TLS error: {exc.__class__.__name__}."
    except requests.exceptions.RequestException as exc:
        result.error = f"Request failed: {exc.__class__.__name__}."
    result.total_ms = int((time.perf_counter() - started) * 1000)
    return result
