from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {"fbclid", "gclid", "spm"}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("evidence URL must be absolute HTTP(S)")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    netloc = parts.hostname.lower()
    if parts.port and not ((parts.scheme == "http" and parts.port == 80) or (parts.scheme == "https" and parts.port == 443)):
        netloc = f"{netloc}:{parts.port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(sorted(query)), ""))


def evidence_hash(canonical_url: str, title: str, snippet: str) -> str:
    material = "\n".join((canonical_url, title.strip(), snippet.strip()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def deduplicate_evidence(items):
    unique = {}
    for item in items:
        unique.setdefault(item.canonical_url, item)
    return list(unique.values())
