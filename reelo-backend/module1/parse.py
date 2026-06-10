"""Robust chunk parsing + validation (module-1 §9).

The happy path is native structured output: the provider returns JSON honouring
``ScriptRequest.json_schema`` and parsing is just ``json.loads`` + Pydantic.
This module is the **safety net** for when a model wraps the JSON in prose, uses
the sentinel fallback, or emits mildly malformed JSON (trailing commas, smart
quotes).

Parse order (stop at the first success), per §9:
    sentinel block  ->  ```json fence  ->  balanced braces
    then json.loads, then json.loads(repair_json(...)).

Validation gates the hard invariant Module 2 depends on: a chunk yields exactly
``expected_count`` segments, ``index`` runs contiguously from ``idx_start``, and
``narration`` / ``image_prompt`` are non-empty.
"""

from __future__ import annotations

import json
import re
from typing import Any

from models.spec import SegmentSpec

SENTINEL_OPEN = "<<<REELO_SPEC>>>"
SENTINEL_CLOSE = "<<<END_REELO_SPEC>>>"

# A “smart quote” → ASCII map used by repair_json.
_SMART_QUOTES = {
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "„": '"',
    "‟": '"',
    "«": '"',
    "»": '"',
}

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


class ParseError(Exception):
    """No JSON block could be located/decoded from the model output."""


class ValidationError(Exception):
    """A decoded chunk violated the structural invariants (count/index/empty)."""


# --------------------------------------------------------------------------- #
# Extraction helpers                                                          #
# --------------------------------------------------------------------------- #
def extract_between(raw: str, open_tag: str, close_tag: str) -> str | None:
    """Return the substring between ``open_tag`` and ``close_tag`` (exclusive)."""
    start = raw.find(open_tag)
    if start == -1:
        return None
    start += len(open_tag)
    end = raw.find(close_tag, start)
    if end == -1:
        return None
    block = raw[start:end].strip()
    return block or None


def extract_json_fence(raw: str) -> str | None:
    """Return the first ```` ```json ... ``` ```` (or bare ```` ``` ```` ) block body."""
    m = _JSON_FENCE_RE.search(raw)
    if not m:
        return None
    block = m.group(1).strip()
    return block or None


def extract_balanced_braces(raw: str) -> str | None:
    """Return the first balanced ``{...}`` span, ignoring braces inside strings."""
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None  # unbalanced (likely truncated)


def repair_json(block: str) -> str:
    """Best-effort repair of common LLM JSON defects.

    - normalises smart quotes to ASCII,
    - strips a UTF-8 BOM,
    - removes trailing commas before ``}`` / ``]``.

    This is a last resort applied only after a plain ``json.loads`` failed.
    """
    repaired = block.lstrip("﻿")
    for bad, good in _SMART_QUOTES.items():
        repaired = repaired.replace(bad, good)
    repaired = _TRAILING_COMMA_RE.sub(r"\1", repaired)
    return repaired


# --------------------------------------------------------------------------- #
# Parse + validate                                                            #
# --------------------------------------------------------------------------- #
def parse_chunk(raw: str) -> dict[str, Any]:
    """Locate and decode the JSON object in ``raw`` (module-1 §9).

    Raises:
        ParseError: when no JSON block can be found or decoded.
    """
    if not raw or not raw.strip():
        raise ParseError("empty model output")

    block = (
        extract_between(raw, SENTINEL_OPEN, SENTINEL_CLOSE)
        or extract_json_fence(raw)
        or extract_balanced_braces(raw)
    )
    if block is None:
        raise ParseError("no JSON block found in model output")

    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        try:
            data = json.loads(repair_json(block))
        except json.JSONDecodeError as exc:  # truly unrecoverable
            raise ParseError(f"could not decode JSON block: {exc}") from exc

    if not isinstance(data, dict):
        raise ParseError("decoded JSON is not an object")
    return data


def validate_chunk(
    data: dict[str, Any], expected_count: int, idx_start: int
) -> list[SegmentSpec]:
    """Validate a decoded chunk into a contiguous list of :class:`SegmentSpec`.

    Enforces (module-1 §9): the chunk has a ``segments`` array of exactly
    ``expected_count`` items, their ``index`` is the contiguous run
    ``idx_start .. idx_start + expected_count - 1``, and ``narration`` /
    ``image_prompt`` are non-empty after stripping. ``image_label`` is filled
    from a slug of the prompt when the model omits it.

    Raises:
        ValidationError: on any structural violation.
    """
    segs_raw = data.get("segments")
    if not isinstance(segs_raw, list):
        raise ValidationError("missing or non-array 'segments'")

    segments: list[SegmentSpec] = []
    for i, item in enumerate(segs_raw):
        if not isinstance(item, dict):
            raise ValidationError(f"segment {i} is not an object")
        payload = dict(item)
        # Tolerate a missing image_label (Open Q #5: AI generates, backend
        # guarantees) — derive a slug from the prompt so validation can proceed.
        if not str(payload.get("image_label") or "").strip():
            payload["image_label"] = slugify(str(payload.get("image_prompt", "")))
        # image_query is optional (web-photo providers): normalise "" -> None so
        # the client's query-selection fallbacks kick in cleanly.
        iq = str(payload.get("image_query") or "").strip()
        payload["image_query"] = iq or None
        try:
            segments.append(SegmentSpec(**payload))
        except Exception as exc:  # noqa: BLE001 — Pydantic type/field errors
            raise ValidationError(f"segment {i} failed field validation: {exc}") from exc

    if len(segments) != expected_count:
        raise ValidationError(
            f"wrong segment count: got {len(segments)}, expected {expected_count}"
        )

    expected_indices = list(range(idx_start, idx_start + expected_count))
    if [s.index for s in segments] != expected_indices:
        raise ValidationError(
            f"index not contiguous/aligned: got {[s.index for s in segments]}, "
            f"expected {expected_indices}"
        )

    for s in segments:
        if not s.narration.strip():
            raise ValidationError(f"segment {s.index}: empty narration")
        if not s.image_prompt.strip():
            raise ValidationError(f"segment {s.index}: empty image_prompt")

    return segments


# --------------------------------------------------------------------------- #
# Slug / file-label helper (Open Q #5)                                        #
# --------------------------------------------------------------------------- #
def slugify(text: str, *, max_words: int = 5) -> str:
    """Produce a lowercase ``a-z0-9-`` slug from the first words of ``text``."""
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    slug = "-".join(words[:max_words])
    return slug or "scene"


__all__ = [
    "SENTINEL_OPEN",
    "SENTINEL_CLOSE",
    "ParseError",
    "ValidationError",
    "extract_between",
    "extract_json_fence",
    "extract_balanced_braces",
    "repair_json",
    "parse_chunk",
    "validate_chunk",
    "slugify",
]
