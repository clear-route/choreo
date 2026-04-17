"""choreo-reporter — pytest plugin that emits a test-report directory at the end
of every pytest run (PRD-007).

This package is a consumer-facing sibling of `choreo`; `choreo` itself has no
dependency on pytest, and this package contributes the reporting pipeline
through the `choreo._reporting` observer seam plus standard pytest hooks.

Public surface is small: installing the package registers the plugin via
a pytest11 entry point. Advanced consumers may register custom redactors
via `register_redactor(...)`.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("choreo-reporter")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"


from ._redact import register_redactor, unregister_redactor

__all__ = ["__version__", "register_redactor", "unregister_redactor"]
