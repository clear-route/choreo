"""Matcher Strategy pattern — ADR-0013.

Pure predicates over decoded payloads. Every matcher implements the same
Protocol; composition happens via `all_of`, `any_of`, `not_`, `every`,
`any_element`.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Pattern, Protocol, runtime_checkable

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path-scope sentinels used in MatchFailure.path (CL3).
#
# A MatchFailure's `path` is overloaded: sometimes it is a payload path
# (e.g. "order.qty"), sometimes a scope label (`<root>`, `<raw>`, `all_of`,
# `any_of`). Hoisted to constants so reporter-side comparisons are not
# string-typed magic.
# ---------------------------------------------------------------------------

ROOT_PATH = "<root>"
RAW_PATH = "<raw>"
COMPOSED_ALL = "all_of"
COMPOSED_ANY = "any_of"

_REROOTABLE = frozenset({ROOT_PATH, COMPOSED_ALL, COMPOSED_ANY})


# ---------------------------------------------------------------------------
# Closed taxonomy of ways a matcher can reject a payload. Every built-in
# matcher — and every custom matcher that wants its rejection to render
# deterministically in the report — emits a `MatchFailure` of one of these
# kinds. Adding a new kind is a typed change, not a string tweak.
# ---------------------------------------------------------------------------

FailureKind = Literal[
    "missing",        # path not present in payload
    "mismatch",       # equality comparison failed
    "type_mismatch",  # comparison could not be performed (wrong type)
    "predicate",      # declarative predicate (in_, gt, lt, payload_contains, not_)
    "composed",       # all_of / any_of / nested contains_fields
]


@dataclass(frozen=True)
class MatchFailure:
    """Structured reason a matcher rejected a payload.

    Replaces the free-form `MatchResult.reason` string for report rendering
    purposes: the reporter serialises this verbatim, and the UI composes any
    human-readable prose from the typed fields — never from a message string.

    `path` is a dot-joined field path (or `"<root>"` for pathless matchers
    and top-level composed predicates). `expected` mirrors the matcher's
    `expected_shape()` — the machine-readable description of what was
    required. `actual` is the sub-payload at `path`, or `None` when the path
    was absent (disambiguated by `kind="missing"`). `children` carries the
    child failures for `composed` (empty otherwise).
    """
    kind: FailureKind
    path: str
    expected: Any
    actual: Any = None
    children: tuple["MatchFailure", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    reason: str
    failure: MatchFailure | None = None


# ---------------------------------------------------------------------------
# Protocols (R2 — ISP split).
#
# `Matcher` is the execution contract every matcher satisfies. `Reportable`
# is an optional capability the test-report writer (PRD-007) consumes to
# render a side-by-side expected/actual diff. Splitting them per ISP makes
# it explicit that a custom matcher written before PRD-007 is still a valid
# `Matcher` — it is simply not `Reportable`.
# ---------------------------------------------------------------------------


@runtime_checkable
class Matcher(Protocol):
    description: str

    def match(self, payload: Any) -> MatchResult: ...


@runtime_checkable
class Reportable(Protocol):
    def expected_shape(self) -> Any: ...


def _expected_shape(matcher: Any) -> Any:
    """Safe accessor used by the scenario runtime.

    Custom matchers written before the Reportable capability may not
    implement `expected_shape`; a raised exception indicates a bug in the
    user's implementation. In both cases we fall back to `None` so the
    scenario runtime cannot be broken by a third-party matcher. A raised
    exception is logged at DEBUG so a mysteriously-blank report is a
    rediscoverable breadcrumb in the scenario log (CL5).
    """
    fn = getattr(matcher, "expected_shape", None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception as exc:
        _log.debug(
            "expected_shape() on %s raised %s: %s; falling back to description",
            type(matcher).__name__,
            type(exc).__name__,
            exc,
        )
        return None


def _is_matcher(obj: Any) -> bool:
    """Duck-typed check. A `Matcher` has a callable `match` and a `description`.

    We avoid `isinstance(obj, Matcher)` because the runtime-checkable Protocol
    treats structural match as equivalent to declared intent; a user class
    that happens to have both attributes for unrelated reasons would be
    silently routed through `_deep_compare` as a matcher. The duck-type is
    no more permissive in practice and keeps the check trivially fast.

    `expected_shape` is intentionally not part of this test — it is a
    Reportable capability, not a Matcher requirement (ISP-split per PRD-007).
    """
    return callable(getattr(obj, "match", None)) and hasattr(obj, "description")


# ---------------------------------------------------------------------------
# Path types and normalisation (C1, P1).
#
# A caller may pass a path as:
#   - a dotted string ("order.fills.0.qty") — sugar for the common case;
#   - a bare int (35)                      — tag-value (integer-keyed) map;
#   - a sequence (("trace.id",), ("a", 0)) — canonical form, the only way
#                                            to reach a key containing ".".
# Sequences are normalised to tuples at construction so the match hot-path
# does no per-message path-splitting.
# ---------------------------------------------------------------------------

PathPart = str | int
Path = str | int | Sequence[PathPart]

_MISSING = object()


def _normalise_path(path: Path) -> tuple[PathPart, ...]:
    """Canonicalise a caller's path into a tuple of parts.

    Dotted-string sugar auto-converts numeric segments to ints so
    `"fills.0.px"` walks into a list at index 0; tuple form preserves the
    caller's types verbatim so `("items", "0")` and `("items", 0)` are
    distinguishable (str → dict key, int → list index or int-keyed dict).
    """
    if isinstance(path, str):
        out: list[PathPart] = []
        for p in path.split("."):
            if p and (p.isdigit() or (len(p) > 1 and p[0] == "-" and p[1:].isdigit())):
                out.append(int(p))
            else:
                out.append(p)
        return tuple(out)
    if isinstance(path, int):
        return (path,)
    if isinstance(path, (bytes, bytearray)):
        raise TypeError(
            f"path must be str, int, or a sequence of str|int; got {type(path).__name__}"
        )
    parts = tuple(path)
    for p in parts:
        if not isinstance(p, (str, int)):
            raise TypeError(
                f"path parts must be str or int; got {type(p).__name__} in {path!r}"
            )
    return parts


def _join_path(parts: Sequence[PathPart]) -> str:
    if not parts:
        return ROOT_PATH
    return ".".join(str(p) for p in parts)


def _describe_path(raw: Path) -> str:
    """Render the caller's original path for display in descriptions and
    `expected_shape()` keys. Joins sequences with dots; leaves bare forms
    as-is."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, int):
        return str(raw)
    return ".".join(str(p) for p in raw)


def _lookup(payload: Any, parts: Sequence[PathPart]) -> Any:
    """Walk a payload by pre-normalised parts. Returns `_MISSING` when absent.

    Strict type-directed traversal: a str part only matches a dict key; an
    int part indexes a list (and falls back to an int dict key, e.g. a
    tag-value map keyed by tag number). Dotted-string sugar converts numeric segments to int at
    normalise time, so a caller writing `"fills.0.px"` still reaches the
    list at index 0 with no ambiguity against a dict whose key happens to
    be the string `"0"`.
    """
    node: Any = payload
    for part in parts:
        if isinstance(node, dict):
            if part in node:
                node = node[part]
                continue
            return _MISSING
        if isinstance(node, list):
            if isinstance(part, int):
                try:
                    node = node[part]
                    continue
                except IndexError:
                    return _MISSING
            return _MISSING
        return _MISSING
    return node


# ---------------------------------------------------------------------------
# Unified predicate engine (R3, P1).
#
# One dataclass handles every field_* matcher AND every pathless value
# matcher. The previous ten near-identical dataclasses collapsed into this
# single form. Path can be empty (pathless), a tuple of parts, or a single
# scalar — normalised at construction.
# ---------------------------------------------------------------------------


def _op_eq(actual: Any, expected: Any) -> bool:
    return actual == expected


def _op_ne(actual: Any, expected: Any) -> bool:
    return actual != expected


def _op_in(actual: Any, expected: tuple[Any, ...]) -> bool:
    return actual in expected


def _op_gt(actual: Any, expected: Any) -> bool:
    return actual > expected


def _op_lt(actual: Any, expected: Any) -> bool:
    return actual < expected


def _op_matches(actual: Any, expected: Pattern[str]) -> bool:
    if not isinstance(actual, str):
        return False
    return expected.fullmatch(actual) is not None


# op_key -> (predicate_fn, is_comparable, failure_kind_when_rejected)
_OPS: dict[str, tuple[Callable[[Any, Any], bool], bool, FailureKind]] = {
    "eq":      (_op_eq,      False, "mismatch"),
    "ne":      (_op_ne,      False, "predicate"),
    "in":      (_op_in,      False, "predicate"),
    "gt":      (_op_gt,      True,  "predicate"),
    "lt":      (_op_lt,      True,  "predicate"),
    "matches": (_op_matches, False, "predicate"),
}


def _render_expected(op: str, value: Any) -> Any:
    """JSON-shaped rendering of an operator + value for MatchFailure and
    expected_shape() output. Normalised across all ops (R5)."""
    if op == "eq":
        return value
    if op == "in":
        return {"in": list(value)}
    if op == "matches":
        return {"matches": value.pattern if hasattr(value, "pattern") else value}
    if op == "exists":
        return {"exists": True}
    return {op: value}


# Human-readable op symbol used in reason strings and descriptions; separate
# from the JSON-shape op_key so typed output and prose can evolve
# independently.
_OP_SYMBOL = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "lt": "<",
    "in": "in",
    "matches": "matches",
}


@dataclass(frozen=True)
class _Predicate:
    """Every field_* and pathless value matcher.

    `parts=()` is the pathless form (applies predicate directly to payload).
    `raw_path` preserves the caller's original form for description /
    expected_shape rendering; the hot-path walk uses `parts`.
    """
    parts: tuple[PathPart, ...]
    raw_path: Path | None
    op: str
    op_value: Any
    description: str

    def match(self, payload: Any) -> MatchResult:
        if self.parts:
            actual = _lookup(payload, self.parts)
            path_str = _join_path(self.parts)
        else:
            actual = payload
            path_str = ROOT_PATH

        if self.op == "exists":
            if actual is _MISSING:
                return MatchResult(
                    False,
                    f"field {_describe_path(self.raw_path) if self.raw_path else path_str!r} missing",
                    MatchFailure("missing", path_str, {"exists": True}),
                )
            return MatchResult(True, f"{path_str} present")

        expected = _render_expected(self.op, self.op_value)

        if actual is _MISSING:
            return MatchResult(
                False,
                f"field {_describe_path(self.raw_path) if self.raw_path else path_str!r} missing",
                MatchFailure("missing", path_str, expected),
            )

        fn, comparable, kind = _OPS[self.op]
        sym = _OP_SYMBOL[self.op]
        path_label = path_str if self.parts else ""
        try:
            ok = fn(actual, self.op_value)
        except TypeError as exc:
            if not comparable:
                raise
            return MatchResult(
                False,
                f"cannot compare {path_label}={actual!r} {sym} {self.op_value!r}: {exc}".lstrip(),
                MatchFailure("type_mismatch", path_str, expected, actual),
            )

        if ok:
            msg = f"{path_label} {sym} {self.op_value!r}".lstrip() if self.parts \
                else f"{sym} {self.op_value!r}"
            return MatchResult(True, msg)

        if self.parts:
            reason = f"expected {path_label} {sym} {self.op_value!r}, got {actual!r}"
        else:
            reason = f"expected {sym} {self.op_value!r}, got {actual!r}"
        return MatchResult(
            False,
            reason,
            MatchFailure(kind, path_str, expected, actual),
        )

    def expected_shape(self) -> Any:
        rendered = _render_expected(self.op, self.op_value)
        if not self.parts:
            return rendered
        key = _describe_path(self.raw_path) if self.raw_path is not None else _join_path(self.parts)
        return {key: rendered}


# ---------------------------------------------------------------------------
# Field-predicate factories (R3; C1 via _normalise_path).
# ---------------------------------------------------------------------------


def _field(raw_path: Path, op: str, op_value: Any, desc_suffix: str) -> Matcher:
    parts = _normalise_path(raw_path)
    return _Predicate(
        parts=parts,
        raw_path=raw_path,
        op=op,
        op_value=op_value,
        description=f"{_describe_path(raw_path)} {desc_suffix}",
    )


def field_equals(path: Path, value: Any) -> Matcher:
    return _field(path, "eq", value, f"= {value!r}")


def field_ne(path: Path, value: Any) -> Matcher:
    return _field(path, "ne", value, f"!= {value!r}")


def field_in(path: Path, values: Iterable[Any]) -> Matcher:
    values_t = tuple(values)
    return _field(path, "in", values_t, f"in {values_t}")


def field_gt(path: Path, value: Any) -> Matcher:
    return _field(path, "gt", value, f"> {value!r}")


def field_lt(path: Path, value: Any) -> Matcher:
    return _field(path, "lt", value, f"< {value!r}")


def field_exists(path: Path) -> Matcher:
    parts = _normalise_path(path)
    return _Predicate(
        parts=parts,
        raw_path=path,
        op="exists",
        op_value=True,
        description=f"exists({_describe_path(path)})",
    )


def field_matches(path: Path, pattern: str | Pattern[str]) -> Matcher:
    compiled = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
    return _field(path, "matches", compiled, f"matches {compiled.pattern!r}")


# ---------------------------------------------------------------------------
# Pathless value-matcher factories (for leaves inside contains_fields).
# ---------------------------------------------------------------------------


def _pathless(op: str, op_value: Any, description: str) -> Matcher:
    return _Predicate(
        parts=(),
        raw_path=None,
        op=op,
        op_value=op_value,
        description=description,
    )


def eq(value: Any) -> Matcher:
    return _pathless("eq", value, f"== {value!r}")


def ne(value: Any) -> Matcher:
    return _pathless("ne", value, f"!= {value!r}")


def in_(values: Iterable[Any]) -> Matcher:
    values_t = tuple(values)
    return _pathless("in", values_t, f"in {values_t}")


def gt(value: Any) -> Matcher:
    return _pathless("gt", value, f"> {value!r}")


def lt(value: Any) -> Matcher:
    return _pathless("lt", value, f"< {value!r}")


def matches(pattern: str | Pattern[str]) -> Matcher:
    compiled = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
    return _pathless("matches", compiled, f"matches {compiled.pattern!r}")


def exists() -> Matcher:
    """Pathless counterpart to field_exists. Only meaningful as a leaf inside
    `contains_fields`, where a sub-payload might be absent; at top level a
    message always has a payload and exists() always matches."""
    return _Predicate(
        parts=(),
        raw_path=None,
        op="exists",
        op_value=True,
        description="exists()",
    )


# ---------------------------------------------------------------------------
# payload_contains — raw-bytes escape hatch (P4 guard).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PayloadContains:
    substring: bytes
    description: str

    def match(self, payload: Any) -> MatchResult:
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError(
                "payload_contains requires a bytes payload; "
                f"got {type(payload).__name__}. If you have a decoded payload, "
                "use field_equals / field_matches / contains_fields instead."
            )
        raw = bytes(payload)
        if self.substring in raw:
            return MatchResult(True, f"contains {self.substring!r}")
        return MatchResult(
            False,
            f"does not contain {self.substring!r}",
            MatchFailure(
                "predicate",
                RAW_PATH,
                {"payload_contains_hex": self.substring.hex()},
                raw,
            ),
        )

    def expected_shape(self) -> Any:
        return {"payload_contains_hex": self.substring.hex()}


def payload_contains(substring: bytes) -> Matcher:
    return _PayloadContains(
        substring=substring, description=f"contains {substring!r}"
    )


# ---------------------------------------------------------------------------
# Composition: all_of / any_of / not_ (R5 shape normalisation).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AllOf:
    matchers: tuple[Matcher, ...]
    description: str

    def match(self, payload: Any) -> MatchResult:
        for m in self.matchers:
            r = m.match(payload)
            if not r.matched:
                child_failure = r.failure or MatchFailure(
                    "predicate", ROOT_PATH, m.description, None
                )
                return MatchResult(
                    False,
                    f"all_of failed on {m.description}: {r.reason}",
                    MatchFailure(
                        "composed",
                        COMPOSED_ALL,
                        self.expected_shape(),
                        None,
                        (child_failure,),
                    ),
                )
        return MatchResult(True, self.description)

    def expected_shape(self) -> Any:
        return {"all_of": [_expected_shape(m) for m in self.matchers]}


def all_of(*matchers: Matcher) -> Matcher:
    desc = "all_of(" + ", ".join(m.description for m in matchers) + ")"
    return _AllOf(matchers=tuple(matchers), description=desc)


@dataclass(frozen=True)
class _AnyOf:
    matchers: tuple[Matcher, ...]
    description: str

    def match(self, payload: Any) -> MatchResult:
        reasons: list[str] = []
        child_failures: list[MatchFailure] = []
        for m in self.matchers:
            r = m.match(payload)
            if r.matched:
                return MatchResult(True, f"any_of matched {m.description}")
            reasons.append(r.reason)
            child_failures.append(
                r.failure
                or MatchFailure("predicate", ROOT_PATH, m.description, None)
            )
        return MatchResult(
            False,
            f"any_of: none matched — {'; '.join(reasons)}",
            MatchFailure(
                "composed",
                COMPOSED_ANY,
                self.expected_shape(),
                None,
                tuple(child_failures),
            ),
        )

    def expected_shape(self) -> Any:
        return {"any_of": [_expected_shape(m) for m in self.matchers]}


def any_of(*matchers: Matcher) -> Matcher:
    desc = "any_of(" + ", ".join(m.description for m in matchers) + ")"
    return _AnyOf(matchers=tuple(matchers), description=desc)


@dataclass(frozen=True)
class _Not:
    inner: Matcher
    description: str

    def match(self, payload: Any) -> MatchResult:
        r = self.inner.match(payload)
        if r.matched:
            return MatchResult(
                False,
                f"not_({self.inner.description}) but it matched",
                MatchFailure(
                    "predicate",
                    ROOT_PATH,
                    {"not": _expected_shape(self.inner)},
                    payload,
                ),
            )
        return MatchResult(True, f"not_({self.inner.description})")

    def expected_shape(self) -> Any:
        return {"not": _expected_shape(self.inner)}


def not_(matcher: Matcher) -> Matcher:
    return _Not(inner=matcher, description=f"not_({matcher.description})")


# ---------------------------------------------------------------------------
# List quantifiers: every / any_element (CL2).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Every:
    inner: Matcher
    description: str

    def match(self, payload: Any) -> MatchResult:
        if not isinstance(payload, list):
            return MatchResult(
                False,
                f"every requires a list payload, got {type(payload).__name__}",
                MatchFailure(
                    "type_mismatch",
                    ROOT_PATH,
                    {"type": "list"},
                    type(payload).__name__,
                ),
            )
        for i, item in enumerate(payload):
            r = self.inner.match(item)
            if not r.matched:
                child = r.failure or MatchFailure(
                    "predicate", ROOT_PATH, self.inner.description, item
                )
                rerooted = _reroot(child, str(i))
                return MatchResult(
                    False,
                    f"every: element {i} failed — {r.reason}",
                    MatchFailure(
                        "composed",
                        str(i),
                        self.expected_shape(),
                        None,
                        (rerooted,),
                    ),
                )
        return MatchResult(True, f"every element satisfies {self.inner.description}")

    def expected_shape(self) -> Any:
        return {"every": _expected_shape(self.inner)}


def every(matcher: Matcher) -> Matcher:
    return _Every(inner=matcher, description=f"every({matcher.description})")


@dataclass(frozen=True)
class _AnyElement:
    inner: Matcher
    description: str

    def match(self, payload: Any) -> MatchResult:
        if not isinstance(payload, list):
            return MatchResult(
                False,
                f"any_element requires a list payload, got {type(payload).__name__}",
                MatchFailure(
                    "type_mismatch",
                    ROOT_PATH,
                    {"type": "list"},
                    type(payload).__name__,
                ),
            )
        child_failures: list[MatchFailure] = []
        for i, item in enumerate(payload):
            r = self.inner.match(item)
            if r.matched:
                return MatchResult(
                    True, f"any_element: index {i} satisfies {self.inner.description}"
                )
            child = r.failure or MatchFailure(
                "predicate", ROOT_PATH, self.inner.description, item
            )
            child_failures.append(_reroot(child, str(i)))
        return MatchResult(
            False,
            f"any_element: no element satisfies {self.inner.description}",
            MatchFailure(
                "composed",
                ROOT_PATH,
                self.expected_shape(),
                None,
                tuple(child_failures),
            ),
        )

    def expected_shape(self) -> Any:
        return {"any_element": _expected_shape(self.inner)}


def any_element(matcher: Matcher) -> Matcher:
    return _AnyElement(inner=matcher, description=f"any_element({matcher.description})")


# ---------------------------------------------------------------------------
# Recursive rerooting (C2).
#
# A user matcher embedded inside a walking container has no idea where it
# was called from; its MatchFailure carries `<root>` (or a composition
# label like `all_of`) for its own paths. When the walker resolves the
# failure, every `<root>`/`all_of`/`any_of` in the tree is rewritten to
# the walked path. Children recurse.
# ---------------------------------------------------------------------------


def _reroot(failure: MatchFailure, new_path: str) -> MatchFailure:
    if failure.path in _REROOTABLE:
        rewritten_path = new_path
    else:
        rewritten_path = failure.path
    new_children = tuple(_reroot(c, new_path) for c in failure.children)
    if rewritten_path == failure.path and new_children == failure.children:
        return failure
    return MatchFailure(
        kind=failure.kind,
        path=rewritten_path,
        expected=failure.expected,
        actual=failure.actual,
        children=new_children,
    )


# ---------------------------------------------------------------------------
# contains_fields — recursive subset match for nested structures.
#
# Pre-compiled at construction (P2): walking the spec once builds a tree of
# plan nodes so the hot path does not re-classify each spec node on every
# incoming message.
# ---------------------------------------------------------------------------

_PLAN_MATCHER = "matcher"
_PLAN_DICT = "dict"
_PLAN_LIST = "list"
_PLAN_LITERAL = "literal"


@dataclass(frozen=True)
class _PlanNode:
    kind: str                          # one of _PLAN_*
    matcher: Matcher | None            # for _PLAN_MATCHER
    literal: Any                       # for _PLAN_LITERAL
    dict_children: tuple[tuple[Any, "_PlanNode"], ...]  # for _PLAN_DICT
    list_children: tuple["_PlanNode", ...]              # for _PLAN_LIST


def _compile(spec: Any) -> _PlanNode:
    if _is_matcher(spec):
        return _PlanNode(_PLAN_MATCHER, spec, None, (), ())
    if isinstance(spec, dict):
        return _PlanNode(
            _PLAN_DICT,
            None,
            None,
            tuple((k, _compile(v)) for k, v in spec.items()),
            (),
        )
    if isinstance(spec, list):
        return _PlanNode(
            _PLAN_LIST,
            None,
            None,
            (),
            tuple(_compile(v) for v in spec),
        )
    return _PlanNode(_PLAN_LITERAL, None, spec, (), ())


@dataclass(frozen=True)
class _ContainsFields:
    spec: Any
    plan: _PlanNode
    description: str

    def match(self, payload: Any) -> MatchResult:
        mismatch = _run_plan(payload, self.plan, prefix=())
        if mismatch is None:
            return MatchResult(True, f"contains {_describe(self.spec)}")
        field_path, detail, failure = mismatch
        return MatchResult(
            False,
            f"at {field_path}: {detail}",
            failure,
        )

    def expected_shape(self) -> Any:
        return self.spec


def contains_fields(spec: Any) -> Matcher:
    """Matcher that recursively checks every key/value in `spec` appears in the
    payload. Lists match positionally; the payload may be longer than the spec.

    Spec leaves may be literals or `Matcher` instances — a matcher leaf is
    handed the sub-payload at its position, so `{"order": {"qty": gt(0)}}`
    asserts "the `qty` nested inside `order` passes `gt(0)`"."""
    return _ContainsFields(
        spec=spec,
        plan=_compile(spec),
        description=f"contains_fields({_describe(spec)})",
    )


def _run_plan(
    payload: Any, node: _PlanNode, prefix: tuple[PathPart, ...]
) -> tuple[str, str, MatchFailure] | None:
    """Return None if `payload` contains what `node` requires; otherwise the
    (path, reason, MatchFailure) triple for the first mismatch."""
    path_str = _join_path(prefix) if prefix else ROOT_PATH

    if node.kind == _PLAN_MATCHER:
        matcher = node.matcher
        assert matcher is not None
        result = matcher.match(payload)
        if result.matched:
            return None
        child = result.failure or MatchFailure(
            "predicate", path_str, matcher.description, payload
        )
        return (path_str, result.reason, _reroot(child, path_str))

    if node.kind == _PLAN_DICT:
        if not isinstance(payload, dict):
            return (
                path_str,
                f"expected dict, got {type(payload).__name__}",
                MatchFailure(
                    "type_mismatch", path_str, {"type": "dict"}, type(payload).__name__
                ),
            )
        for key, child_node in node.dict_children:
            child_prefix = prefix + (key,)
            child_path = _join_path(child_prefix)
            if key not in payload:
                return (
                    child_path,
                    f"key missing; expected {_plan_describe(child_node)}",
                    MatchFailure(
                        "missing",
                        child_path,
                        _plan_expected(child_node),
                    ),
                )
            mismatch = _run_plan(payload[key], child_node, child_prefix)
            if mismatch is not None:
                return mismatch
        return None

    if node.kind == _PLAN_LIST:
        if not isinstance(payload, list):
            return (
                path_str,
                f"expected list, got {type(payload).__name__}",
                MatchFailure(
                    "type_mismatch", path_str, {"type": "list"}, type(payload).__name__
                ),
            )
        if len(payload) < len(node.list_children):
            return (
                path_str,
                f"list shorter than spec: {len(payload)} < {len(node.list_children)}",
                MatchFailure(
                    "predicate",
                    path_str,
                    {"min_length": len(node.list_children)},
                    payload,
                ),
            )
        for i, child_node in enumerate(node.list_children):
            child_prefix = prefix + (i,)
            mismatch = _run_plan(payload[i], child_node, child_prefix)
            if mismatch is not None:
                return mismatch
        return None

    # _PLAN_LITERAL
    if payload == node.literal:
        return None
    return (
        path_str,
        f"expected {node.literal!r}, got {payload!r}",
        MatchFailure("mismatch", path_str, node.literal, payload),
    )


def _plan_expected(node: _PlanNode) -> Any:
    """Machine-readable expected-shape for a sub-plan (used in 'missing' failures)."""
    if node.kind == _PLAN_MATCHER:
        assert node.matcher is not None
        return _expected_shape(node.matcher)
    if node.kind == _PLAN_LITERAL:
        return node.literal
    if node.kind == _PLAN_DICT:
        return {k: _plan_expected(c) for k, c in node.dict_children}
    # _PLAN_LIST
    return [_plan_expected(c) for c in node.list_children]


def _plan_describe(node: _PlanNode) -> str:
    if node.kind == _PLAN_MATCHER:
        assert node.matcher is not None
        return node.matcher.description
    if node.kind == _PLAN_LITERAL:
        return repr(node.literal)
    if node.kind == _PLAN_DICT:
        return _describe({k: _plan_expected(c) for k, c in node.dict_children})
    return _describe([_plan_expected(c) for c in node.list_children])


def _describe(value: Any) -> str:
    if _is_matcher(value):
        return value.description
    if isinstance(value, dict) and len(value) > 3:
        keys = list(value)[:3]
        return "{" + ", ".join(f"{k!r}: ..." for k in keys) + ", ...}"
    return repr(value)
