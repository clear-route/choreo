"""Allowlist loading and enforcement exceptions (ADR-0006).

The library owns the **Allowlist primitive** — a generic category-keyed
list-of-strings. It does not own the category names. Each transport decides
which categories it enforces; the YAML file carries whatever categories the
deployment's transports collectively need.

For a NATS deployment the allowlist might have `nats_servers`. For Kafka,
`kafka_brokers` and optionally `client_ids`. For RabbitMQ, `amqp_brokers`.
The library does not prescribe which. Transports validate their own
configured values against the categories they care about; unknown categories
in the file are ignored silently (unused, not rejected) so one file can
cover several transports.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class AllowlistError(Exception):
    """Base for allowlist-guard failures. Subclasses carry the specifics."""


class HostNotInAllowlist(AllowlistError):
    """A configured value (NATS server, Kafka broker, …) is not on the allowlist."""


class AllowlistConfigError(AllowlistError):
    """The allowlist YAML is malformed."""


@dataclass(frozen=True)
class Allowlist:
    """Per-deployment allowlist. Categories are transport-defined strings
    mapping to permitted values.

    Accessed via `allowlist.get(category)` which returns an empty tuple for
    unknown categories — the transport decides whether "unknown category"
    means "nothing permitted" (strict) or "skip the check" (by not calling
    `get()` at all).
    """

    categories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    source_path: Path | None = None

    def get(self, category: str) -> tuple[str, ...]:
        """Values permitted under `category`. Empty tuple if not declared."""
        return self.categories.get(category, ())

    def enforce(
        self,
        category: str,
        values: Iterable[str],
        *,
        label: str,
        normalise: Callable[[str], str] | None = None,
    ) -> None:
        """Raise `HostNotInAllowlist` if any `value` is not permitted under
        `category`.

        `label` is the human-readable noun used in the error message
        (e.g. ``"NATS server"``, ``"Kafka broker"``). `normalise`, when given,
        is applied to each value before the permit-check — a value is accepted
        if either its raw or normalised form appears in the allowlist. Kafka
        uses this to accept both ``host:port`` and ``kafka://host:port``.
        """
        permitted = self.get(category)
        for value in values:
            if value in permitted:
                continue
            if normalise is not None and normalise(value) in permitted:
                continue
            raise HostNotInAllowlist(
                f"{label} {value!r} is not on the allowlist at {self.source_path}"
            )


def load_allowlist(path: Path) -> Allowlist:
    """Load a YAML allowlist. The file is a flat mapping of category name to
    list of strings. Any top-level value that is not a list is rejected.

    Example:

        nats_servers: ["nats://localhost:4222"]
        kafka_brokers: ["localhost:9092"]
    """
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise AllowlistConfigError(f"{path} is not valid YAML: {e}") from e

    if not isinstance(raw, dict):
        raise AllowlistConfigError(f"{path} top-level must be a mapping, got {type(raw).__name__}")

    categories: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if not isinstance(value, list):
            raise AllowlistConfigError(
                f"{path}: value for {key!r} must be a list, got {type(value).__name__}"
            )
        categories[str(key)] = tuple(str(v) for v in value)

    return Allowlist(categories=categories, source_path=path)
