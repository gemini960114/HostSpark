"""Backward compatibility adapter for media_resolver."""
from hostspark.telegram.media import (
    DOC_EXTENSIONS,
    IMAGE_EXTENSIONS,
    LOCAL_PATH_RE,
    URL_RE,
    detect_output_media,
    fetch_ssrf_safe_media,
    is_ssrf_safe_url,
)

__all__ = [
    "DOC_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "LOCAL_PATH_RE",
    "URL_RE",
    "detect_output_media",
    "fetch_ssrf_safe_media",
    "is_ssrf_safe_url",
]
