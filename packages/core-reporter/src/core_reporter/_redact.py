"""Credential-shape redaction — PRD-007 §12.

Default redactor strips the values of fields whose *key* matches a
credential-shape regex, and scrubs `bearer <token>` / `x-api-key: <token>`
style tokens from freeform text (stdout, stderr, log, traceback).

Consumers who need domain-level PII redaction register additional
redactors via `register_redactor(...)`. The default redactor always runs
first; consumer redactors run after so they can layer on top of the
cleaned shape.

The redactor counts what it changed so the report surfaces a
`run.redactions` summary and the author knows the report is not a 1:1
copy of their data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


# Case-insensitive regex over keys. Intentionally broad; false positives
# are a mild inconvenience, false negatives are an incident.
_CREDENTIAL_KEY = re.compile(
    r"^(password|passwd|pwd|secret|token|api[_-]?key|authorization|"
    r"auth|cookie|bearer|private[_-]?key|client[_-]?secret|access[_-]?token|"
    r"refresh[_-]?token|session[_-]?id)$",
    re.IGNORECASE,
)


# Inline patterns for freeform text. Each pattern captures a token value
# after a recognisable prefix; the redactor replaces the captured group
# with `<redacted>`.
_STREAM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # bearer <token>
    re.compile(r"(bearer\s+)([A-Za-z0-9._\-]+)", re.IGNORECASE),
    # Authorization: <scheme> <token>
    re.compile(
        r"(authorization\s*[:=]\s*\w+\s+)([A-Za-z0-9._\-]+)", re.IGNORECASE
    ),
    # x-api-key: <value>   /   api-key=<value>
    re.compile(
        r"(x-api-key\s*[:=]\s*)([A-Za-z0-9._\-]+)", re.IGNORECASE
    ),
    re.compile(
        r"(api[_-]?key\s*[:=]\s*)([A-Za-z0-9._\-]+)", re.IGNORECASE
    ),
    # password=<value>
    re.compile(r"(password\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE),
)


REDACTED = "<redacted>"


@dataclass
class RedactionStats:
    fields: int = 0
    stream_matches: int = 0


@dataclass
class _RedactorRegistry:
    consumer_redactors: list[Callable[[Any], Any]] = field(default_factory=list)


_registry = _RedactorRegistry()


def register_redactor(fn: Callable[[Any], Any]) -> None:
    """Register a consumer-supplied redactor applied after the built-in one.

    The callable receives the already-cleaned structured value and returns
    a replacement. It is invoked for each top-level payload the reporter
    serialises (scenario payload, traceback string, etc.); consumers
    scope their own redaction inside that function.
    """
    if fn not in _registry.consumer_redactors:
        _registry.consumer_redactors.append(fn)


def unregister_redactor(fn: Callable[[Any], Any]) -> None:
    try:
        _registry.consumer_redactors.remove(fn)
    except ValueError:
        pass


def _clear_consumer_redactors_for_test() -> None:
    _registry.consumer_redactors.clear()


def redact_structured(value: Any, stats: RedactionStats) -> Any:
    """Walk a JSON-shaped value, replacing credential-key field values.

    Dicts, lists, tuples, and sets are traversed. Non-container leaves
    are returned unchanged. Cyclic structures would loop here, but a
    well-formed scenario payload is already an acyclic decoded JSON
    tree; if a user passes something weirder we fall back to `repr()`
    elsewhere before reaching this function.
    """
    redacted = _redact_structured_inner(value, stats)
    for consumer in list(_registry.consumer_redactors):
        try:
            redacted = consumer(redacted)
        except Exception:
            # A consumer redactor must not break the report.
            pass
    return redacted


def _redact_structured_inner(value: Any, stats: RedactionStats) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _CREDENTIAL_KEY.match(k):
                out[k] = REDACTED
                stats.fields += 1
            else:
                out[k] = _redact_structured_inner(v, stats)
        return out
    if isinstance(value, list):
        return [_redact_structured_inner(v, stats) for v in value]
    if isinstance(value, tuple):
        return [_redact_structured_inner(v, stats) for v in value]
    return value


def redact_stream(text: str, stats: RedactionStats) -> str:
    """Replace inline credential-value tokens in freeform text."""
    if not text:
        return text
    out = text
    for pattern in _STREAM_PATTERNS:
        def _sub(match: re.Match[str]) -> str:
            stats.stream_matches += 1
            return match.group(1) + REDACTED
        out = pattern.sub(_sub, out)
    return out
