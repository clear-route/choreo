#!/usr/bin/env python3
"""Extract the release notes for a given version from CHANGELOG.md.

Usage:
    python scripts/extract_changelog.py v0.1.0 > RELEASE_NOTES.md

Reads CHANGELOG.md from the repo root (relative to this script's location).
Prints the content between the heading for the requested version and the
next version heading (or end of file).  Exits non-zero if the version is
not found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def extract(version: str) -> str:
    """Return the changelog section for *version*."""
    # Strip leading 'v' if present so both 'v0.1.0' and '0.1.0' work.
    version = version.lstrip("v")

    changelog = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    if not changelog.exists():
        print(f"error: {changelog} not found", file=sys.stderr)
        sys.exit(1)

    text = changelog.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find the heading line: ## [0.1.0] - 2026-04-19
    heading_re = re.compile(rf"^## \[{re.escape(version)}\]")
    start = None
    for i, line in enumerate(lines):
        if heading_re.match(line):
            start = i + 1
            break

    if start is None:
        print(f"error: version {version!r} not found in {changelog}", file=sys.stderr)
        sys.exit(1)

    # Collect until the next ## heading or EOF.
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break

    section = "\n".join(lines[start:end]).strip()
    return section


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <version>", file=sys.stderr)
        sys.exit(1)
    print(extract(sys.argv[1]))
