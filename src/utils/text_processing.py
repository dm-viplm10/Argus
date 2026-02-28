"""Text cleaning, deduplication, and normalization utilities."""

from __future__ import annotations

import hashlib
import re
import unicodedata


def normalize_text(text: str) -> str:
    """Normalize unicode, collapse whitespace, strip."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_entity_name(name: str) -> str:
    """Normalize an entity name for deduplication."""
    name = normalize_text(name)
    name = re.sub(r"\b(Inc|LLC|Corp|Ltd|Co|LP|GP)\.?\b", "", name, flags=re.IGNORECASE)
    return name.strip().strip(",").strip()


def content_hash(text: str) -> str:
    """SHA-256 hash of text content for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def truncate_content(text: str, max_chars: int = 50_000) -> str:
    """Truncate text to max chars, adding a marker if truncated."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... content truncated ...]"


def deduplicate_by_field(items: list[dict], field: str) -> list[dict]:
    """Remove duplicates from a list of dicts based on a specific field."""
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        key = str(item.get(field, "")).lower().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result
