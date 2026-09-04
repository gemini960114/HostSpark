import ipaddress
import logging
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx


logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s)\]\"'<>]+", re.IGNORECASE)
LOCAL_PATH_RE = re.compile(r"(?:/|~/)[A-Za-z0-9_.\- /]+\.[A-Za-z0-9]{2,6}")
from hostspark.constants import (
    AUDIO_EXTENSIONS,
    DOC_EXTENSIONS,
    IMAGE_EXTENSIONS,
    SAFE_EXTENSIONS,
    SSRF_MEDIA_DOWNLOAD_MAX_BYTES,
    SSRF_MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
    VIDEO_EXTENSIONS,
)

__all__ = [
    "AUDIO_EXTENSIONS",
    "DOC_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "SAFE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "SSRF_MEDIA_DOWNLOAD_MAX_BYTES",
    "SSRF_MEDIA_DOWNLOAD_TIMEOUT_SECONDS",
    "detect_output_media",
    "fetch_ssrf_safe_media",
    "is_ssrf_safe_url",
]


def is_ssrf_safe_url(url_str: str) -> bool:
    try:
        parsed = urlparse(url_str)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = parsed.hostname
        if not host:
            return False

        # Attempt to parse literal IP first
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False
        except ValueError:
            # Domain name, resolve DNS
            try:
                addr_info = socket.getaddrinfo(host, None)
                for entry in addr_info:
                    raw_ip = entry[4][0]
                    ip = ipaddress.ip_address(raw_ip)
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                        return False
            except (socket.gaierror, socket.herror, Exception) as exc:
                logger.debug("DNS 解析失敗：%s (%s)", host, exc)
                return False
        return True
    except Exception as exc:
        logger.debug("URL 驗證失敗：%s (%s)", url_str, exc)
        return False


async def fetch_ssrf_safe_media(
    url_str: str,
    timeout_seconds: float = SSRF_MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes | None:
    if not is_ssrf_safe_url(url_str):
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            resp = await client.get(url_str)
            if resp.status_code == 200:
                if len(resp.content) <= SSRF_MEDIA_DOWNLOAD_MAX_BYTES:
                    return resp.content
    except Exception as exc:
        logger.debug("下載媒體失敗：%s (%s)", url_str, exc)
    return None


def detect_output_media(
    text: str,
    allowed_dirs: list[Path],
) -> tuple[list[Path], list[str]]:
    resolved_dirs = [d.expanduser().resolve() for d in allowed_dirs]
    matched_paths: list[Path] = []
    matched_urls: list[str] = []

    # 1. Match local paths
    for match in LOCAL_PATH_RE.finditer(text):
        raw_path = match.group(0).strip()
        try:
            p = Path(raw_path).expanduser().resolve()
            if p.is_file():
                if any(p == d or d in p.parents for d in resolved_dirs):
                    if p not in matched_paths:
                        matched_paths.append(p)
        except Exception:
            pass

    # 2. Match URLs
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(").,;'\"")
        try:
            parsed = urlparse(url)
            path = parsed.path.lower()
            if any(path.endswith(ext) for ext in IMAGE_EXTENSIONS):
                if is_ssrf_safe_url(url) and url not in matched_urls:
                    matched_urls.append(url)
        except Exception:
            continue

    return matched_paths, matched_urls
