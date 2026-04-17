"""HTML template — PRD-007 §4, §7, §9.

Renders a self-contained `index.html` with inlined CSS, vanilla JS, and
the run's JSON payload. The HTML never performs runtime `fetch`; the
JSON lives in a `<script type="application/json">` block and is parsed
by the bundled JS via `textContent`.

Rendering contract (PRD-007 §9):

  - All payload-derived content is injected via `textContent` or
    `document.createTextNode`. `innerHTML`, `outerHTML`,
    `insertAdjacentHTML`, `document.write`, `eval`, and `new Function`
    are forbidden anywhere in the bundled JS.
  - The inlined JSON is escaped: `<` → `\\u003c`, `\\u2028` and
    `\\u2029` replaced with their `\\u...` forms. This prevents
    `</script>` break-out and parser incompatibilities.
  - Every selector in the bundled CSS is scoped under `.harness-report`.

`render_html(results_json_text)` returns the complete HTML as a string.
`FORBIDDEN_JS_SINKS` is the list the rendering-contract lint uses to
guard future edits.
"""
from __future__ import annotations


FORBIDDEN_JS_SINKS: tuple[str, ...] = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
)


def escape_for_inline_json(text: str) -> str:
    """Escape JSON text for safe embedding in `<script type="application/json">`.

    Replaces `<` (so `</script>` cannot close the element), `\\u2028`
    and `\\u2029` (JavaScript parser incompatibilities that were valid
    JSON until ES2019).
    """
    return (
        text.replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_html(results_json_text: str) -> str:
    escaped = escape_for_inline_json(results_json_text)
    return _HTML_TEMPLATE.replace("__HARNESS_RESULTS_JSON__", escaped)


_CSS = """
/* Tokens — single source of truth. See .design-engineer/system.md. */
.harness-report {
  --hr-bg: oklch(0.99 0 0);
  --hr-surface: oklch(0.98 0 0);
  --hr-surface-2: oklch(0.965 0 0);
  --hr-border: oklch(0.91 0 0);
  --hr-border-strong: oklch(0.83 0 0);
  --hr-text: oklch(0.18 0 0);
  --hr-text-muted: oklch(0.50 0.01 250);
  --hr-text-subtle: oklch(0.67 0.01 250);
  --hr-pass: oklch(0.62 0.15 150);
  --hr-pass-tint: oklch(0.96 0.05 150);
  --hr-pass-border: oklch(0.70 0.15 150);
  --hr-fail: oklch(0.62 0.18 25);
  --hr-fail-tint: oklch(0.96 0.05 25);
  --hr-fail-border: oklch(0.72 0.18 25);
  --hr-slow: oklch(0.70 0.15 75);
  --hr-slow-tint: oklch(0.96 0.05 75);
  --hr-slow-border: oklch(0.78 0.15 75);
  --hr-timeout: oklch(0.55 0.18 15);
  --hr-timeout-tint: oklch(0.96 0.05 15);
  --hr-timeout-border: oklch(0.65 0.18 15);
  --hr-skip: oklch(0.67 0.01 250);
  --hr-skip-tint: oklch(0.96 0 0);
  --hr-info: oklch(0.60 0.15 245);
  --hr-info-tint: oklch(0.96 0.05 245);
  --hr-info-border: oklch(0.70 0.15 245);
  --hr-badge-fg: oklch(1 0 0);
  --hr-font-sans: ui-sans-serif, -apple-system, "Segoe UI", Inter, system-ui, sans-serif;
  --hr-font-mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Monaco, Consolas, monospace;
  --hr-text-xs: 11px;  --hr-lh-xs: 1.4;
  --hr-text-sm: 12px;  --hr-lh-sm: 1.45;
  --hr-text-base: 13px; --hr-lh-base: 1.5;
  --hr-text-md: 15px; --hr-lh-md: 1.4;
  --hr-text-lg: 20px; --hr-lh-lg: 1.3;
  --hr-space-1: 4px; --hr-space-2: 8px; --hr-space-3: 12px;
  --hr-space-4: 16px; --hr-space-5: 20px; --hr-space-6: 24px;
  --hr-space-8: 32px; --hr-space-10: 40px;
  --hr-radius-sm: 3px; --hr-radius: 5px; --hr-radius-lg: 8px;
  --hr-ease: cubic-bezier(0.2, 0, 0, 1);
  --hr-dur-fast: 120ms; --hr-dur-medium: 180ms;
  --hr-shadow-md: 0 8px 24px -6px oklch(0 0 0 / 0.12), 0 2px 8px -2px oklch(0 0 0 / 0.06);
}

@media (prefers-color-scheme: dark) {
  .harness-report {
    --hr-bg: oklch(0.14 0 0);
    --hr-surface: oklch(0.17 0 0);
    --hr-surface-2: oklch(0.21 0 0);
    --hr-border: oklch(0.28 0 0);
    --hr-border-strong: oklch(0.38 0 0);
    --hr-text: oklch(0.92 0 0);
    --hr-text-muted: oklch(0.72 0.01 250);
    --hr-text-subtle: oklch(0.55 0.01 250);
    --hr-pass: oklch(0.74 0.15 150);
    --hr-pass-tint: oklch(0.22 0.04 150);
    --hr-fail: oklch(0.72 0.18 25);
    --hr-fail-tint: oklch(0.22 0.06 25);
    --hr-slow: oklch(0.78 0.15 75);
    --hr-slow-tint: oklch(0.24 0.06 75);
    --hr-timeout: oklch(0.70 0.18 15);
    --hr-timeout-tint: oklch(0.22 0.06 15);
    --hr-info: oklch(0.72 0.15 245);
    --hr-info-tint: oklch(0.22 0.06 245);
    --hr-badge-fg: oklch(0.14 0 0);
  }
}

/* Reset + base */
.harness-report { font-family: var(--hr-font-sans); color: var(--hr-text); background: var(--hr-bg); position: fixed; inset: 0; display: flex; flex-direction: column; font-size: var(--hr-text-base); line-height: var(--hr-lh-base); }
.harness-report * { box-sizing: border-box; }
.harness-report *:focus { outline: none; }
.harness-report *:focus-visible { outline: 2px solid var(--hr-info); outline-offset: 2px; border-radius: var(--hr-radius-sm); }

/* Header — run-level aggregate */
.harness-report .hr-header { background: var(--hr-surface); border-bottom: 1px solid var(--hr-border); padding: var(--hr-space-5) var(--hr-space-6); position: sticky; top: 0; z-index: 10; }
.harness-report .hr-hero-project { font-size: var(--hr-text-xs); text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; color: var(--hr-text-subtle); margin-bottom: var(--hr-space-2); }
.harness-report .hr-hero-project[hidden] { display: none; }
.harness-report .hr-hero { display: flex; align-items: baseline; gap: var(--hr-space-4); }
.harness-report .hr-hero-sentence { flex: 0 1 auto; font-size: var(--hr-text-lg); font-weight: 400; margin: 0; letter-spacing: -0.02em; color: var(--hr-text-muted); line-height: 1.2; }
.harness-report .hr-hero-sentence strong { color: var(--hr-text); font-weight: 600; font-variant-numeric: tabular-nums; }
.harness-report .hr-hero-sentence[data-outcome="fail"] strong[data-field="failed-count"], .harness-report .hr-hero-sentence[data-outcome="errored"] strong[data-field="failed-count"] { color: var(--hr-fail); }
.harness-report .hr-hero-sentence[data-outcome="slow"] strong[data-field="slow-count"] { color: var(--hr-slow); }
.harness-report .hr-hero-sentence[data-outcome="pass"] strong[data-field="passed-count"] { color: var(--hr-pass); }
.harness-report .hr-hero-credit { font-size: var(--hr-text-sm); color: var(--hr-text-subtle); white-space: nowrap; }
.harness-report .hr-hero-credit a { color: var(--hr-text-muted); text-decoration: none; font-weight: 600; }
.harness-report .hr-hero-credit a:hover { color: var(--hr-info); text-decoration: underline; }
.harness-report .hr-hero-right { display: flex; gap: var(--hr-space-3); align-items: center; margin-left: auto; color: var(--hr-text-muted); font-size: var(--hr-text-sm); font-variant-numeric: tabular-nums; }
.harness-report .hr-hero-duration { font-family: var(--hr-font-mono); }

/* Proportion bar — pass/slow/fail/errored/skipped segments side-by-side. */
.harness-report .hr-proportion { display: flex; height: 6px; margin-top: var(--hr-space-3); border-radius: 3px; overflow: hidden; background: var(--hr-surface-2); }
.harness-report .hr-proportion span { height: 100%; transition: width var(--hr-dur-medium) var(--hr-ease); }
.harness-report .hr-proportion span[data-seg="passed"] { background: var(--hr-pass); }
.harness-report .hr-proportion span[data-seg="slow"] { background: var(--hr-slow); }
.harness-report .hr-proportion span[data-seg="failed"] { background: var(--hr-fail); }
.harness-report .hr-proportion span[data-seg="errored"] { background: var(--hr-fail); filter: brightness(0.8); }
.harness-report .hr-proportion span[data-seg="skipped"] { background: var(--hr-skip); }

/* Sub-line — non-zero counts + latency + timestamp, all muted weight. */
.harness-report .hr-hero-sub { display: flex; gap: var(--hr-space-3); flex-wrap: wrap; align-items: center; margin-top: var(--hr-space-2); font-size: var(--hr-text-sm); color: var(--hr-text-muted); font-variant-numeric: tabular-nums; }
.harness-report .hr-hero-sub .hr-sub-sep { color: var(--hr-text-subtle); opacity: 0.5; }
.harness-report .hr-hero-sub span[hidden] { display: none; }
.harness-report .hr-hero-sub span[data-field="total"] { color: var(--hr-text-muted); }
.harness-report .hr-hero-sub span[data-field="passed"] { color: var(--hr-pass); }
.harness-report .hr-hero-sub span[data-field="failed"], .harness-report .hr-hero-sub span[data-field="errored"] { color: var(--hr-fail); font-weight: 600; }
.harness-report .hr-hero-sub span[data-field="slow"] { color: var(--hr-slow); font-weight: 600; }
.harness-report .hr-hero-sub span[data-field="skipped"] { color: var(--hr-skip); }
.harness-report .hr-hero-sub span[data-field^="p"] { font-family: var(--hr-font-mono); font-size: var(--hr-text-xs); color: var(--hr-text-subtle); }
.harness-report .hr-hero-sub .hr-sub-ts { color: var(--hr-text-subtle); }

/* About — popover (no backdrop, click-outside to close). */
.harness-report .hr-about { position: relative; }
.harness-report .hr-about-toggle { font: inherit; font-size: var(--hr-text-sm); padding: var(--hr-space-1) var(--hr-space-2); border: 1px solid var(--hr-border); background: var(--hr-bg); border-radius: var(--hr-radius-sm); cursor: pointer; color: var(--hr-text-muted); display: inline-flex; align-items: center; gap: var(--hr-space-1); transition: background-color var(--hr-dur-fast) var(--hr-ease); }
.harness-report .hr-about-toggle span[aria-hidden] { display: inline-flex; width: 14px; height: 14px; border-radius: 50%; border: 1px solid currentColor; align-items: center; justify-content: center; font-size: 9px; font-family: serif; font-style: italic; line-height: 1; }
.harness-report .hr-about-toggle:hover { background: var(--hr-surface-2); color: var(--hr-text); }
.harness-report .hr-about-toggle[aria-expanded="true"] { background: var(--hr-surface-2); color: var(--hr-text); }
.harness-report .hr-about-panel { position: absolute; top: calc(100% + var(--hr-space-2)); right: 0; min-width: 320px; max-width: 420px; background: var(--hr-bg); border: 1px solid var(--hr-border); border-radius: var(--hr-radius); box-shadow: var(--hr-shadow-md); padding: var(--hr-space-3) var(--hr-space-4); z-index: 20; font-size: var(--hr-text-sm); }
.harness-report .hr-about-panel dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 4px var(--hr-space-3); align-items: baseline; }
.harness-report .hr-about-panel dt { color: var(--hr-text-subtle); font-size: var(--hr-text-xs); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }
.harness-report .hr-about-panel dd { margin: 0; color: var(--hr-text); font-family: var(--hr-font-mono); font-size: var(--hr-text-xs); word-break: break-all; }
.harness-report .hr-about-panel dd[hidden], .harness-report .hr-about-panel dt[hidden] { display: none; }

/* Toolbar */
.harness-report .hr-toolbar { background: var(--hr-surface); border-bottom: 1px solid var(--hr-border); padding: var(--hr-space-2) var(--hr-space-6); display: flex; gap: var(--hr-space-4); align-items: center; flex-wrap: wrap; font-size: var(--hr-text-base); }
.harness-report .hr-toolbar label { display: flex; gap: var(--hr-space-2); align-items: center; color: var(--hr-text-muted); font-size: var(--hr-text-sm); }
.harness-report .hr-toolbar input, .harness-report .hr-toolbar select { font: inherit; color: var(--hr-text); padding: var(--hr-space-1) var(--hr-space-2); border: 1px solid var(--hr-border); border-radius: var(--hr-radius-sm); background: var(--hr-bg); transition: border-color var(--hr-dur-fast) var(--hr-ease); }
.harness-report .hr-toolbar input:hover, .harness-report .hr-toolbar select:hover { border-color: var(--hr-border-strong); }
.harness-report .hr-toolbar input[type="number"] { width: 80px; }
.harness-report .hr-toolbar input[type="search"] { width: 220px; }
.harness-report .hr-filters { display: flex; gap: var(--hr-space-1); }
.harness-report .hr-filters button { font: inherit; font-size: var(--hr-text-sm); padding: var(--hr-space-1) var(--hr-space-3); border: 1px solid var(--hr-border); background: var(--hr-bg); border-radius: var(--hr-radius-sm); cursor: pointer; color: var(--hr-text-muted); transition: background-color var(--hr-dur-fast) var(--hr-ease), color var(--hr-dur-fast) var(--hr-ease); }
.harness-report .hr-filters button:hover { background: var(--hr-surface-2); color: var(--hr-text); }
.harness-report .hr-filters button[aria-pressed="true"] { background: var(--hr-info-tint); color: var(--hr-info); border-color: var(--hr-info-border); }
.harness-report .hr-announce { font-size: var(--hr-text-sm); color: var(--hr-text-subtle); margin-left: auto; font-variant-numeric: tabular-nums; }

/* Keyboard-shortcut legend. `<details>` in the toolbar; click the
   summary to reveal a small popover listing the bindings a power
   user needs. Single source of truth for key discoverability. */
.harness-report .hr-keys { position: relative; font-size: var(--hr-text-sm); }
.harness-report .hr-keys > summary { list-style: none; cursor: pointer; font: inherit; font-size: var(--hr-text-sm); padding: var(--hr-space-1) var(--hr-space-2); border: 1px solid var(--hr-border); background: var(--hr-bg); border-radius: var(--hr-radius-sm); color: var(--hr-text-muted); user-select: none; display: inline-flex; gap: var(--hr-space-1); align-items: center; transition: background-color var(--hr-dur-fast) var(--hr-ease), color var(--hr-dur-fast) var(--hr-ease); }
.harness-report .hr-keys > summary span[aria-hidden] { display: inline-flex; width: 14px; height: 14px; align-items: center; justify-content: center; font-size: 12px; line-height: 1; }
.harness-report .hr-keys > summary::-webkit-details-marker { display: none; }
.harness-report .hr-keys > summary:hover { background: var(--hr-surface-2); color: var(--hr-text); }
.harness-report .hr-keys[open] > summary { background: var(--hr-surface-2); color: var(--hr-text); border-color: var(--hr-border-strong); }
.harness-report .hr-keys-panel { position: absolute; top: calc(100% + 4px); right: 0; min-width: 240px; background: var(--hr-surface); border: 1px solid var(--hr-border-strong); border-radius: var(--hr-radius); padding: var(--hr-space-3) var(--hr-space-4); z-index: 20; }
.harness-report .hr-keys-panel h4 { margin: 0 0 var(--hr-space-2) 0; font-size: 10px; color: var(--hr-text-muted); text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; }
.harness-report .hr-keys-panel dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: var(--hr-space-2) var(--hr-space-3); align-items: baseline; }
.harness-report .hr-keys-panel dt { margin: 0; }
.harness-report .hr-keys-panel dd { margin: 0; color: var(--hr-text-muted); font-size: var(--hr-text-sm); }
.harness-report .hr-keys-panel kbd { font-family: var(--hr-font-mono); font-size: var(--hr-text-xs); padding: 1px var(--hr-space-2); border: 1px solid var(--hr-border); border-bottom-width: 2px; border-radius: var(--hr-radius-sm); background: var(--hr-bg); color: var(--hr-text); min-width: 18px; display: inline-block; text-align: center; }

/* Body layout. Tree width is driven by a CSS custom property so the
   user can drag the resizer to change it and the value persists across
   runs via localStorage. Backslash toggles collapse globally.  */
.harness-report .hr-body { display: flex; flex: 1 1 auto; min-height: 0; }
.harness-report .hr-tree { flex: 0 0 auto; width: var(--hr-tree-width, 32%); min-width: 0; max-width: 60%; overflow-y: auto; background: var(--hr-surface); border-right: 1px solid var(--hr-border); padding: var(--hr-space-3) 0; }
.harness-report[data-tree-collapsed="true"] .hr-tree { width: 0; padding: 0; border-right: none; overflow: hidden; }

.harness-report .hr-resizer { flex: 0 0 auto; width: 8px; position: relative; cursor: col-resize; background: var(--hr-surface); border-right: 1px solid var(--hr-border); user-select: none; transition: background-color var(--hr-dur-fast) var(--hr-ease); z-index: 1; }
.harness-report .hr-resizer:hover, .harness-report .hr-resizer[data-dragging="true"] { background: var(--hr-info-tint); }
.harness-report .hr-resizer:focus-visible { outline: 2px solid var(--hr-info); outline-offset: -2px; }
/* Grip glyph — three stacked dots centred vertically. Makes the
   resizer a discoverable affordance; without it the rail looks like
   a passive border. */
.harness-report .hr-resizer::before { content: '\u22ee'; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: var(--hr-text-subtle); font-size: 14px; line-height: 1; pointer-events: none; letter-spacing: 0; }
.harness-report .hr-resizer:hover::before, .harness-report .hr-resizer[data-dragging="true"]::before { color: var(--hr-info); }
/* Collapsed state: the resizer becomes a slim reopen rail with a chevron. */
.harness-report[data-tree-collapsed="true"] .hr-resizer { cursor: e-resize; }
.harness-report[data-tree-collapsed="true"] .hr-resizer::before { content: '\u203a'; color: var(--hr-text-muted); font-size: 14px; font-weight: 600; top: var(--hr-space-4); transform: translate(-50%, 0); }

.harness-report .hr-detail { flex: 1 1 auto; overflow-y: auto; padding: var(--hr-space-5) var(--hr-space-6); background: var(--hr-bg); }

/* Tree — files + tests */
.harness-report .hr-file { margin-bottom: var(--hr-space-1); }
.harness-report .hr-file-header { padding: var(--hr-space-2) var(--hr-space-3); font-weight: 600; font-size: var(--hr-text-sm); cursor: pointer; display: flex; align-items: center; gap: var(--hr-space-2); color: var(--hr-text); transition: background-color var(--hr-dur-fast) var(--hr-ease); font-family: var(--hr-font-mono); border-radius: var(--hr-radius-sm); user-select: none; }
.harness-report .hr-file-header:hover { background: var(--hr-surface-2); }
.harness-report .hr-file-header:hover .hr-caret { color: var(--hr-text); }
.harness-report .hr-caret { display: inline-flex; width: 14px; height: 14px; align-items: center; justify-content: center; color: var(--hr-text-muted); font-size: 11px; transition: transform var(--hr-dur-medium) var(--hr-ease), color var(--hr-dur-fast) var(--hr-ease); flex-shrink: 0; }
.harness-report .hr-file[aria-expanded="false"] .hr-caret { transform: rotate(-90deg); }
.harness-report .hr-file[aria-expanded="false"] .hr-tests { display: none; }
.harness-report .hr-file-name { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 1px; line-height: 1.25; }
.harness-report .hr-file-dir { font-size: var(--hr-text-xs); color: var(--hr-text-muted); font-weight: 400; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left; }
.harness-report .hr-file-basename { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.harness-report .hr-file-count { color: var(--hr-text-subtle); font-size: var(--hr-text-xs); font-variant-numeric: tabular-nums; font-weight: 400; }
.harness-report .hr-file-fail-count { background: var(--hr-fail); color: var(--hr-badge-fg); font-size: 10px; font-weight: 600; padding: 1px var(--hr-space-2); border-radius: 8px; min-width: 16px; text-align: center; font-variant-numeric: tabular-nums; line-height: 1.3; }
.harness-report .hr-file-fail-count[data-kind="slow"] { background: var(--hr-slow); color: oklch(0.20 0.06 75); }
.harness-report .hr-bulk { display: flex; gap: var(--hr-space-1); margin-left: var(--hr-space-2); }
.harness-report .hr-bulk button { font: inherit; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; padding: 2px var(--hr-space-2); border: 1px solid var(--hr-border); background: transparent; border-radius: var(--hr-radius-sm); cursor: pointer; color: var(--hr-text-subtle); transition: background-color var(--hr-dur-fast) var(--hr-ease), color var(--hr-dur-fast) var(--hr-ease); }
.harness-report .hr-bulk button:hover { background: var(--hr-surface-2); color: var(--hr-text); }
.harness-report .hr-test { padding: var(--hr-space-1) var(--hr-space-3) var(--hr-space-1) var(--hr-space-8); cursor: pointer; display: flex; gap: var(--hr-space-2); align-items: center; font-size: var(--hr-text-base); border-left: 2px solid transparent; transition: background-color var(--hr-dur-fast) var(--hr-ease); }
.harness-report .hr-test:hover { background: var(--hr-surface-2); }
.harness-report .hr-test[data-selected="true"] { background: var(--hr-surface-2); border-left-color: var(--hr-info); }
.harness-report .hr-test[data-outcome="failed"] { border-left-color: var(--hr-fail); background: var(--hr-fail-tint); }
.harness-report .hr-test[data-outcome="errored"] { border-left-color: var(--hr-fail); background: var(--hr-fail-tint); }
.harness-report .hr-test[data-outcome="slow"] { border-left-color: var(--hr-slow); background: var(--hr-slow-tint); }
.harness-report .hr-test[data-visible="false"] { display: none; }

/* Badges — icon + hue, never colour-alone */
.harness-report .hr-badge { display: inline-flex; min-width: 18px; height: 18px; padding: 0 var(--hr-space-2); border-radius: var(--hr-radius-sm); font-size: 10px; font-weight: 700; align-items: center; justify-content: center; color: var(--hr-badge-fg); font-family: var(--hr-font-mono); letter-spacing: 0; line-height: 1; gap: 3px; }
.harness-report .hr-badge[data-outcome="passed"], .harness-report .hr-badge[data-outcome="pass"] { background: var(--hr-pass); }
.harness-report .hr-badge[data-outcome="failed"], .harness-report .hr-badge[data-outcome="fail"] { background: var(--hr-fail); }
.harness-report .hr-badge[data-outcome="errored"] { background: var(--hr-fail); filter: brightness(0.85); }
.harness-report .hr-badge[data-outcome="skipped"] { background: var(--hr-skip); }
.harness-report .hr-badge[data-outcome="slow"] { background: var(--hr-slow); color: oklch(0.20 0.06 75); }
.harness-report .hr-badge[data-outcome="timeout"] { background: var(--hr-timeout); }
.harness-report .hr-badge[data-outcome="pending"] { background: var(--hr-skip); }

/* Topic chip — first-class element for channel names */
.harness-report .hr-topic { font-family: var(--hr-font-mono); font-size: var(--hr-text-xs); background: var(--hr-surface-2); color: var(--hr-text); padding: 1px var(--hr-space-2); border-radius: var(--hr-radius-sm); border: 1px solid var(--hr-border); }

/* Test list labels */
.harness-report .hr-test-name { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--hr-text); font-family: var(--hr-font-mono); font-size: var(--hr-text-sm); }
.harness-report .hr-test-duration { color: var(--hr-text-subtle); font-size: var(--hr-text-xs); font-variant-numeric: tabular-nums; }

/* Detail pane */
.harness-report .hr-detail h2 { font-size: var(--hr-text-md); margin: 0 0 var(--hr-space-1) 0; font-weight: 600; letter-spacing: -0.01em; }
.harness-report .hr-detail .hr-nodeid { font-family: var(--hr-font-mono); font-size: var(--hr-text-sm); color: var(--hr-text-muted); margin-bottom: var(--hr-space-4); word-break: break-all; }

/* Scenario group — card when multiple scenarios per test, thin
 * section header when solo. Design C: the majority case is solo, so
 * card chrome is only paid for when the test genuinely has more than
 * one scenario. */
.harness-report .hr-scenario { margin-bottom: var(--hr-space-4); }
.harness-report .hr-scenario[data-solo="false"] { border: 1px solid var(--hr-border); border-radius: var(--hr-radius); background: var(--hr-surface); overflow: hidden; }
.harness-report .hr-scenario-header { padding: var(--hr-space-3) var(--hr-space-4); display: flex; gap: var(--hr-space-3); align-items: center; cursor: pointer; transition: background-color var(--hr-dur-fast) var(--hr-ease); }
.harness-report .hr-scenario[data-solo="false"] .hr-scenario-header { border-bottom: 1px solid var(--hr-border); }
.harness-report .hr-scenario[data-solo="true"] .hr-scenario-header { padding: var(--hr-space-2) 0 var(--hr-space-3) 0; border-bottom: 1px solid var(--hr-border); margin-bottom: var(--hr-space-3); cursor: default; }
.harness-report .hr-scenario[data-solo="true"] .hr-scenario-header:hover { background: transparent; }
.harness-report .hr-scenario[data-solo="false"] .hr-scenario-header:hover { background: var(--hr-surface-2); }
.harness-report .hr-scenario[aria-expanded="false"] .hr-scenario-body { display: none; }
.harness-report .hr-scenario[data-solo="false"][aria-expanded="false"] .hr-scenario-header { border-bottom: none; }
.harness-report .hr-scenario-name { font-weight: 600; font-size: var(--hr-text-base); font-family: var(--hr-font-mono); }
.harness-report .hr-scenario[data-solo="true"] .hr-scenario-name { font-weight: 500; color: var(--hr-text-muted); font-size: var(--hr-text-sm); }
.harness-report .hr-scenario[data-solo="true"] .hr-scenario-name::before { content: "scenario "; color: var(--hr-text-subtle); font-weight: 400; }
.harness-report .hr-scenario-meta { color: var(--hr-text-subtle); font-size: var(--hr-text-xs); margin-left: auto; display: flex; gap: var(--hr-space-3); font-variant-numeric: tabular-nums; }
.harness-report .hr-scenario-meta span { color: var(--hr-text-muted); }
.harness-report .hr-scenario-body { background: transparent; }
.harness-report .hr-scenario[data-solo="false"] .hr-scenario-body { padding: var(--hr-space-3) var(--hr-space-4); background: var(--hr-bg); }

/* Expectation row — no card chrome. Row is a flex header with an
 * inline chevron disclosure. Expanded body flows directly below the
 * row, bordered only by a subtle top line. */
.harness-report .hr-handle { margin-bottom: var(--hr-space-3); border-top: 1px solid var(--hr-border); padding-top: var(--hr-space-3); }
.harness-report .hr-handle:first-of-type { border-top: none; padding-top: 0; }
.harness-report .hr-handle-header { display: flex; gap: var(--hr-space-3); align-items: center; cursor: pointer; font-size: var(--hr-text-sm); padding: var(--hr-space-1) 0; user-select: none; }
.harness-report .hr-handle-header:hover .hr-caret { color: var(--hr-text); }
.harness-report .hr-handle-header:hover .hr-topic { background: var(--hr-surface-2); }
.harness-report .hr-handle[aria-expanded="false"] .hr-handle-body { display: none; }
.harness-report .hr-handle-kind { color: var(--hr-text-subtle); font-size: var(--hr-text-xs); text-transform: uppercase; letter-spacing: 0.05em; }
.harness-report .hr-handle-topic { /* replaced by .hr-topic */ }
.harness-report .hr-handle-latency { color: var(--hr-text-subtle); font-size: var(--hr-text-xs); font-variant-numeric: tabular-nums; margin-left: auto; font-family: var(--hr-font-mono); }
.harness-report .hr-handle-body { padding: var(--hr-space-3) 0 var(--hr-space-2) var(--hr-space-6); background: transparent; border-top: none; }
.harness-report .hr-handle .hr-caret { color: var(--hr-text-muted); flex-shrink: 0; }
.harness-report .hr-handle[aria-expanded="false"] .hr-caret { transform: rotate(-90deg); }

/* Expected / actual panels (pass case) + structural diff (fail case) */
.harness-report .hr-diff { display: grid; grid-template-columns: 1fr 1fr; gap: var(--hr-space-3); }
.harness-report .hr-diff-panel { background: var(--hr-surface); border: 1px solid var(--hr-border); border-radius: var(--hr-radius-sm); overflow: hidden; }
.harness-report .hr-diff-panel h4 { margin: 0; padding: var(--hr-space-1) var(--hr-space-3); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--hr-text-muted); background: var(--hr-surface-2); border-bottom: 1px solid var(--hr-border); font-weight: 600; }
.harness-report .hr-diff-panel pre { margin: 0; padding: var(--hr-space-2) var(--hr-space-3); font-family: var(--hr-font-mono); font-size: var(--hr-text-xs); line-height: var(--hr-lh-xs); white-space: pre-wrap; word-break: break-word; overflow-x: auto; color: var(--hr-text); }

/* Structural diff — one panel, only differing paths highlighted. */
.harness-report .hr-structural { background: var(--hr-surface); border: 1px solid var(--hr-border); border-radius: var(--hr-radius-sm); overflow: hidden; }
.harness-report .hr-structural-header { padding: var(--hr-space-1) var(--hr-space-3); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--hr-text-muted); background: var(--hr-surface-2); border-bottom: 1px solid var(--hr-border); font-weight: 600; display: flex; gap: var(--hr-space-3); }
.harness-report .hr-structural-header span[data-field="diff-count"] { color: var(--hr-fail); }
.harness-report .hr-structural-body { padding: var(--hr-space-2) var(--hr-space-3); font-family: var(--hr-font-mono); font-size: var(--hr-text-xs); line-height: var(--hr-lh-xs); }
.harness-report .hr-diff-line { padding: 2px var(--hr-space-2); margin: 0 calc(-1 * var(--hr-space-2)); border-radius: var(--hr-radius-sm); display: flex; gap: var(--hr-space-2); align-items: baseline; }
.harness-report .hr-diff-line[data-kind="diff"] { background: var(--hr-fail-tint); }
.harness-report .hr-diff-line[data-kind="missing"] { background: var(--hr-fail-tint); }
.harness-report .hr-diff-line[data-kind="extra"] { background: var(--hr-slow-tint); }
.harness-report .hr-diff-path { color: var(--hr-text-muted); min-width: 90px; }
.harness-report .hr-diff-line[data-kind="diff"] .hr-diff-path { color: var(--hr-fail); font-weight: 600; }
.harness-report .hr-diff-line[data-kind="missing"] .hr-diff-path { color: var(--hr-fail); font-weight: 600; }
.harness-report .hr-diff-line[data-kind="extra"] .hr-diff-path { color: var(--hr-slow); }
.harness-report .hr-diff-value { color: var(--hr-text); }
.harness-report .hr-diff-arrow { color: var(--hr-text-subtle); }
.harness-report .hr-diff-expected { color: var(--hr-text-muted); text-decoration: line-through; }
.harness-report .hr-diff-kind { font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--hr-text-muted); }

/* Budget overshoot bar — shown on SLOW outcome handles. */
.harness-report .hr-budget { margin: var(--hr-space-2) 0; }
.harness-report .hr-budget-label { font-size: var(--hr-text-xs); color: var(--hr-text-muted); font-family: var(--hr-font-mono); margin-bottom: 3px; display: flex; gap: var(--hr-space-2); align-items: baseline; }
.harness-report .hr-budget-label strong { color: var(--hr-slow); font-weight: 600; }
.harness-report .hr-budget-bar { position: relative; height: 6px; background: var(--hr-surface-2); border-radius: 3px; overflow: hidden; }
.harness-report .hr-budget-bar-fill { position: absolute; top: 0; bottom: 0; left: 0; background: var(--hr-pass); }
.harness-report .hr-budget-bar-over { position: absolute; top: 0; bottom: 0; background: var(--hr-slow); }
.harness-report .hr-budget-bar-marker { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--hr-text); opacity: 0.5; }

/* Pass note — condensed message for non-expanded matched expectations. */
.harness-report .hr-pass-note { font-size: var(--hr-text-xs); color: var(--hr-pass); font-family: var(--hr-font-mono); padding: var(--hr-space-1) var(--hr-space-2); background: var(--hr-pass-tint); border-radius: var(--hr-radius-sm); margin-bottom: var(--hr-space-2); }
.harness-report .hr-timeout-note { font-size: var(--hr-text-xs); color: var(--hr-fail); font-family: var(--hr-font-mono); padding: var(--hr-space-1) var(--hr-space-2); background: var(--hr-fail-tint); border-radius: var(--hr-radius-sm); margin-bottom: var(--hr-space-2); }

/* Reason (when the matcher didn't match) + matcher line */
.harness-report .hr-handle-reason { margin-top: var(--hr-space-2); font-size: var(--hr-text-xs); color: var(--hr-fail); padding: var(--hr-space-1) var(--hr-space-2); background: var(--hr-fail-tint); border-radius: var(--hr-radius-sm); }
.harness-report .hr-handle-matcher { margin-top: var(--hr-space-2); font-size: var(--hr-text-xs); color: var(--hr-text-muted); font-family: var(--hr-font-mono); }
.harness-report .hr-handle-matcher code { background: var(--hr-surface-2); padding: 1px var(--hr-space-1); border-radius: var(--hr-radius-sm); color: var(--hr-text); }

/* Timeline — event stream */
.harness-report .hr-timeline { margin-top: var(--hr-space-4); }
.harness-report .hr-timeline h3 { font-size: 10px; margin: 0 0 var(--hr-space-2) 0; color: var(--hr-text-muted); text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; display: flex; justify-content: space-between; align-items: baseline; }
.harness-report .hr-timeline h3 small { text-transform: none; letter-spacing: 0; color: var(--hr-text-subtle); font-weight: 400; font-family: var(--hr-font-mono); }


/* Waterfall timeline — Jaeger-style trace view.
   One row per event; indented by causal depth (test publish → matched,
   test publish → replied → matched → replied → ...). Bar
   in the centre column spans from the parent emitter's offset to this
   event's offset, so the bar's *width* is the propagation latency of
   that single hop. Time axis header aligns with bar track. */
.harness-report .hr-timeline-wrap { position: relative; }

.harness-report .hr-waterfall { margin-top: var(--hr-space-3); border: 1px solid var(--hr-border); border-radius: var(--hr-radius-sm); background: var(--hr-bg); overflow: hidden; }

.harness-report .hr-waterfall-row,
.harness-report .hr-waterfall-axis {
  display: grid;
  /* Label grows to fit the subject; bar track caps at 320px so the
     extra pixels on wide screens go to the topic, not to blank bar
     background. Axis ticks inside the track stay proportional. */
  grid-template-columns: minmax(320px, 1fr) minmax(180px, 320px) 72px;
  align-items: center;
  font-family: var(--hr-font-mono);
  font-size: var(--hr-text-xs);
}

.harness-report .hr-waterfall-axis {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--hr-surface);
  border-bottom: 1px solid var(--hr-border);
  padding: 6px 0;
}
.harness-report .hr-waterfall-axis-label { padding-left: var(--hr-space-3); color: var(--hr-text-subtle); font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
.harness-report .hr-waterfall-axis-track { position: relative; height: 12px; margin: 0 var(--hr-space-2); }
.harness-report .hr-waterfall-axis-tick {
  position: absolute;
  top: 0;
  transform: translateX(-50%);
  color: var(--hr-text-subtle);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.harness-report .hr-waterfall-axis-tick::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 50%;
  width: 1px;
  height: 5px;
  background: var(--hr-border);
}
.harness-report .hr-waterfall-axis-time { padding-right: var(--hr-space-3); text-align: right; color: var(--hr-text-subtle); font-size: 10px; }

.harness-report .hr-waterfall-row {
  padding: 3px 0;
  border-bottom: 1px solid var(--hr-surface-2);
  cursor: pointer;
  transition: background-color var(--hr-dur-fast) var(--hr-ease);
  position: relative;
}
.harness-report .hr-waterfall-row:last-child { border-bottom: none; }
.harness-report .hr-waterfall-row:hover { background: var(--hr-surface-2); }
.harness-report .hr-waterfall-row[data-hovered-depth="true"] { background: var(--hr-surface-2); }
.harness-report .hr-waterfall-row[data-selected="true"] { background: var(--hr-surface-2); box-shadow: inset 2px 0 0 var(--hr-info); }
.harness-report .hr-waterfall-row[data-action="mismatched"] { background: var(--hr-fail-tint); }
.harness-report .hr-waterfall-row[data-action="deadline"] { background: var(--hr-fail-tint); font-weight: 600; }
.harness-report .hr-waterfall-row[data-action="reply_failed"] { background: var(--hr-fail-tint); }
.harness-report .hr-waterfall-row:focus-visible { outline: 2px solid var(--hr-info); outline-offset: -2px; }

.harness-report .hr-waterfall-label { display: flex; align-items: center; gap: var(--hr-space-1); padding-left: var(--hr-space-3); padding-right: var(--hr-space-2); min-width: 0; }
.harness-report .hr-waterfall-indent { display: inline-flex; align-items: stretch; flex-shrink: 0; height: 18px; }
.harness-report .hr-waterfall-indent-line { display: inline-block; width: 14px; border-left: 1px dotted var(--hr-border-strong); }
.harness-report .hr-waterfall-indent-connector { display: inline-flex; align-items: center; justify-content: center; width: 14px; color: var(--hr-text-subtle); font-size: 11px; }
.harness-report .hr-waterfall-topic { color: var(--hr-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
/* Shared prefix/suffix chars that repeat across every row in a
   scenario get dimmed so the eye lands on the variable middle. Full
   subject is still present in the DOM and in the `title` tooltip. */
.harness-report .hr-waterfall-topic-shared { color: var(--hr-text-subtle); }
.harness-report .hr-waterfall-action {
  flex-shrink: 0;
  color: var(--hr-text-muted);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 600;
}
.harness-report .hr-waterfall-action[data-action="published"] { color: var(--hr-info); }
.harness-report .hr-waterfall-action[data-action="received"] { color: var(--hr-text-muted); }
.harness-report .hr-waterfall-action[data-action="matched"] { color: var(--hr-pass); }
.harness-report .hr-waterfall-action[data-action="mismatched"] { color: var(--hr-fail); }
.harness-report .hr-waterfall-action[data-action="deadline"] { color: var(--hr-fail); }
.harness-report .hr-waterfall-action[data-action="replied"] { color: var(--hr-pass); }
.harness-report .hr-waterfall-action[data-action="reply_failed"] { color: var(--hr-fail); }
.harness-report .hr-waterfall-action[data-action="correlation_skipped"] { color: var(--hr-text-subtle); }

/* Bar track — position: relative so absolute-positioned bars + dots
   land at % offsets computed from the scenario's max offset. Faint
   grid lines at 25/50/75 % mirror the axis tick marks. */
.harness-report .hr-waterfall-track {
  position: relative;
  height: 18px;
  margin: 0 var(--hr-space-2);
  background: linear-gradient(to right,
    transparent calc(25% - 1px), var(--hr-surface-2) calc(25% - 1px), var(--hr-surface-2) 25%, transparent 25%,
    transparent calc(50% - 1px), var(--hr-surface-2) calc(50% - 1px), var(--hr-surface-2) 50%, transparent 50%,
    transparent calc(75% - 1px), var(--hr-surface-2) calc(75% - 1px), var(--hr-surface-2) 75%, transparent 75%
  );
}
.harness-report .hr-waterfall-bar {
  position: absolute;
  top: 7px;
  height: 4px;
  border-radius: 2px;
  min-width: 2px;
  background: var(--hr-text-subtle);
  opacity: 0.7;
}
.harness-report .hr-waterfall-bar[data-action="published"] { background: var(--hr-info); }
.harness-report .hr-waterfall-bar[data-action="received"] { background: var(--hr-text-subtle); }
.harness-report .hr-waterfall-bar[data-action="matched"] { background: var(--hr-pass); }
.harness-report .hr-waterfall-bar[data-action="mismatched"] { background: var(--hr-fail); }
.harness-report .hr-waterfall-bar[data-action="deadline"] { background: var(--hr-fail); }
.harness-report .hr-waterfall-bar[data-action="replied"] { background: var(--hr-info); }
.harness-report .hr-waterfall-bar[data-action="reply_failed"] { background: var(--hr-fail); }

.harness-report .hr-waterfall-dot {
  position: absolute;
  top: 5px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  transform: translateX(-50%);
  background: var(--hr-text-subtle);
  box-shadow: 0 0 0 2px var(--hr-bg);
}
.harness-report .hr-waterfall-dot[data-action="published"] { background: var(--hr-info); }
.harness-report .hr-waterfall-dot[data-action="received"] { background: var(--hr-text-subtle); }
.harness-report .hr-waterfall-dot[data-action="matched"] { background: var(--hr-pass); }
.harness-report .hr-waterfall-dot[data-action="mismatched"] { background: var(--hr-fail); }
.harness-report .hr-waterfall-dot[data-action="deadline"] { background: var(--hr-fail); width: 3px; height: 16px; top: 1px; border-radius: 0; }
.harness-report .hr-waterfall-dot[data-action="replied"] { background: var(--hr-info); }
.harness-report .hr-waterfall-dot[data-action="reply_failed"] { background: var(--hr-fail); }

.harness-report .hr-waterfall-time { text-align: right; color: var(--hr-text-subtle); font-variant-numeric: tabular-nums; padding-right: var(--hr-space-3); font-size: 10px; white-space: nowrap; }

/* Inline detail panel revealed on row click — full timestamp, delta
   from parent, topic, wall clock. Spans the full row width. */
.harness-report .hr-waterfall-detail {
  grid-column: 1 / -1;
  padding: var(--hr-space-2) var(--hr-space-3) var(--hr-space-2) var(--hr-space-6);
  background: var(--hr-surface-2);
  border-top: 1px solid var(--hr-border);
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--hr-space-1) var(--hr-space-3);
  font-size: var(--hr-text-xs);
}
.harness-report .hr-waterfall-detail-label { color: var(--hr-text-subtle); text-transform: uppercase; font-size: 10px; letter-spacing: 0.05em; }
.harness-report .hr-waterfall-detail-value { color: var(--hr-text); word-break: break-word; }

/* Reply reports — PRD-008 / ADR-0017 — scope-bound reply summaries,
   rendered between handles and the timeline. One row per reply;
   compact so a multi-reply cascade stays scannable. */
.harness-report .hr-replies { margin-top: var(--hr-space-4); }
.harness-report .hr-replies h3 { font-size: 10px; margin: 0 0 var(--hr-space-2) 0; color: var(--hr-text-muted); text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; }
.harness-report .hr-reply-row { display: grid; grid-template-columns: auto 1fr 1fr auto; gap: var(--hr-space-3); align-items: baseline; padding: 4px var(--hr-space-2); border-bottom: 1px solid var(--hr-surface-2); font-family: var(--hr-font-mono); font-size: var(--hr-text-xs); }
.harness-report .hr-reply-row[data-state="replied"] { color: var(--hr-text); }
.harness-report .hr-reply-row[data-state="reply_failed"] { color: var(--hr-text); background: var(--hr-fail-tint); }
.harness-report .hr-reply-row[data-state="armed_no_match"],
.harness-report .hr-reply-row[data-state="armed_matcher_rejected"] { color: var(--hr-text-muted); background: var(--hr-slow-tint); }
.harness-report .hr-reply-state { font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; font-size: 10px; }
.harness-report .hr-reply-state[data-state="replied"] { color: var(--hr-pass); }
.harness-report .hr-reply-state[data-state="reply_failed"] { color: var(--hr-fail); }
.harness-report .hr-reply-state[data-state="armed_no_match"],
.harness-report .hr-reply-state[data-state="armed_matcher_rejected"] { color: var(--hr-slow); }
.harness-report .hr-reply-topics { display: flex; gap: var(--hr-space-2); align-items: baseline; color: var(--hr-text); }
.harness-report .hr-reply-arrow { color: var(--hr-text-subtle); }
.harness-report .hr-reply-counts { color: var(--hr-text-muted); font-variant-numeric: tabular-nums; text-align: right; }
.harness-report .hr-reply-flags { color: var(--hr-fail); font-weight: 600; font-size: 10px; }

/* Captures (stdout/stderr/log) */
.harness-report .hr-captures { margin-top: var(--hr-space-4); }
.harness-report .hr-captures details { margin-top: var(--hr-space-2); font-size: var(--hr-text-sm); }
.harness-report .hr-captures summary { cursor: pointer; color: var(--hr-text-muted); font-weight: 600; font-size: var(--hr-text-xs); text-transform: uppercase; letter-spacing: 0.05em; }
.harness-report .hr-captures pre { margin: var(--hr-space-2) 0 0 0; padding: var(--hr-space-2) var(--hr-space-3); background: oklch(0.18 0 0); color: oklch(0.90 0 0); border-radius: var(--hr-radius-sm); font-family: var(--hr-font-mono); font-size: var(--hr-text-xs); overflow-x: auto; white-space: pre-wrap; word-break: break-word; }

/* Traceback */
.harness-report .hr-traceback { margin-top: var(--hr-space-4); padding: var(--hr-space-3); background: oklch(0.18 0 0); color: oklch(0.85 0.08 25); border-radius: var(--hr-radius-sm); border-left: 3px solid var(--hr-fail-border); font-family: var(--hr-font-mono); font-size: var(--hr-text-xs); overflow-x: auto; white-space: pre-wrap; }

/* Empty state */
.harness-report .hr-empty { color: var(--hr-text-subtle); padding: var(--hr-space-6); text-align: center; font-style: italic; }

/* Incomplete-workers banner */
.harness-report .hr-incomplete-workers { background: var(--hr-slow-tint); color: var(--hr-slow); padding: var(--hr-space-2) var(--hr-space-4); border-bottom: 1px solid var(--hr-slow-border); font-size: var(--hr-text-sm); }
.harness-report .hr-incomplete-workers[data-visible="false"] { display: none; }

/* JSON highlighter tokens */
.harness-report .hr-json-key { color: var(--hr-info); }
.harness-report .hr-json-string { color: oklch(0.52 0.14 150); }
.harness-report .hr-json-number { color: oklch(0.58 0.16 45); }
.harness-report .hr-json-bool { color: var(--hr-fail); font-weight: 600; }
.harness-report .hr-json-null { color: var(--hr-text-subtle); font-style: italic; }

"""


_JS = r"""
(function () {
  'use strict';

  // -------------------------------------------------------------------
  // Load the inlined JSON via textContent. Never use innerHTML here or
  // anywhere else in this script (PRD-007 §9 rendering contract).
  // -------------------------------------------------------------------
  var dataNode = document.getElementById('harness-results');
  if (!dataNode) { return; }
  var data;
  try {
    data = JSON.parse(dataNode.textContent || '{}');
  } catch (e) {
    return;
  }

  // -------------------------------------------------------------------
  // Safe DOM helpers — textContent only, setAttribute only.
  // -------------------------------------------------------------------
  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k)) {
          n.setAttribute(k, String(attrs[k]));
        }
      }
    }
    if (text !== undefined && text !== null) {
      n.textContent = String(text);
    }
    return n;
  }

  function clear(node) {
    while (node.firstChild) { node.removeChild(node.firstChild); }
  }

  // -------------------------------------------------------------------
  // JSON pretty-printer / highlighter. Tokenises the value and emits
  // individual DOM nodes with textContent. Never builds HTML strings.
  // -------------------------------------------------------------------
  function appendJson(host, value, indent) {
    indent = indent || 0;
    var t = typeof value;
    if (value === null) {
      host.appendChild(el('span', { 'class': 'hr-json-null' }, 'null'));
      return;
    }
    if (t === 'boolean') {
      host.appendChild(el('span', { 'class': 'hr-json-bool' }, value ? 'true' : 'false'));
      return;
    }
    if (t === 'number') {
      host.appendChild(el('span', { 'class': 'hr-json-number' }, String(value)));
      return;
    }
    if (t === 'string') {
      host.appendChild(el('span', { 'class': 'hr-json-string' }, JSON.stringify(value)));
      return;
    }
    if (Array.isArray(value)) {
      if (value.length === 0) {
        host.appendChild(document.createTextNode('[]'));
        return;
      }
      host.appendChild(document.createTextNode('[\n'));
      for (var i = 0; i < value.length; i++) {
        host.appendChild(document.createTextNode(pad(indent + 2)));
        appendJson(host, value[i], indent + 2);
        if (i < value.length - 1) { host.appendChild(document.createTextNode(',')); }
        host.appendChild(document.createTextNode('\n'));
      }
      host.appendChild(document.createTextNode(pad(indent) + ']'));
      return;
    }
    if (t === 'object') {
      var keys = Object.keys(value);
      if (keys.length === 0) {
        host.appendChild(document.createTextNode('{}'));
        return;
      }
      host.appendChild(document.createTextNode('{\n'));
      for (var j = 0; j < keys.length; j++) {
        host.appendChild(document.createTextNode(pad(indent + 2)));
        host.appendChild(el('span', { 'class': 'hr-json-key' }, JSON.stringify(keys[j])));
        host.appendChild(document.createTextNode(': '));
        appendJson(host, value[keys[j]], indent + 2);
        if (j < keys.length - 1) { host.appendChild(document.createTextNode(',')); }
        host.appendChild(document.createTextNode('\n'));
      }
      host.appendChild(document.createTextNode(pad(indent) + '}'));
      return;
    }
    host.appendChild(document.createTextNode(String(value)));
  }

  function pad(n) {
    var s = '';
    for (var i = 0; i < n; i++) { s += ' '; }
    return s;
  }

  // -------------------------------------------------------------------
  // Percentile helpers.
  // -------------------------------------------------------------------
  function percentile(sorted, p) {
    if (sorted.length === 0) { return null; }
    var idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
    return sorted[idx];
  }

  function collectHandleLatencies(tests) {
    var out = [];
    for (var i = 0; i < tests.length; i++) {
      var s = tests[i].scenarios || [];
      for (var j = 0; j < s.length; j++) {
        var h = s[j].handles || [];
        for (var k = 0; k < h.length; k++) {
          if (typeof h[k].latency_ms === 'number') { out.push(h[k].latency_ms); }
        }
      }
    }
    out.sort(function (a, b) { return a - b; });
    return out;
  }

  // -------------------------------------------------------------------
  // State + URL hash.
  // -------------------------------------------------------------------
  var DEFAULT_STATUSES = { passed: false, failed: true, slow: true, skipped: true, errored: true };

  function initialState() {
    var s = {
      statuses: Object.assign({}, DEFAULT_STATUSES),
      search: '',
      marker: '',
      slowerThanMs: null,
      selectedNodeId: null,
      expanded: {}
    };
    readHashInto(s);
    return s;
  }

  function readHashInto(state) {
    var hash = window.location.hash || '';
    if (hash.charAt(0) === '#') { hash = hash.slice(1); }
    if (!hash) { return; }
    var parts = hash.split('&');
    for (var i = 0; i < parts.length; i++) {
      var kv = parts[i].split('=');
      if (kv.length !== 2) { continue; }
      var k = decodeURIComponent(kv[0]);
      var v = decodeURIComponent(kv[1]);
      if (k === 'status') {
        var keep = {};
        var items = v.split(',');
        for (var j = 0; j < items.length; j++) {
          if (items[j]) { keep[items[j]] = true; }
        }
        state.statuses = { passed: !!keep.passed, failed: !!keep.failed, slow: !!keep.slow, skipped: !!keep.skipped, errored: !!keep.errored };
      } else if (k === 'q') {
        state.search = v;
      } else if (k === 'marker') {
        state.marker = v;
      } else if (k === 'slow') {
        var num = parseFloat(v);
        state.slowerThanMs = isFinite(num) ? num : null;
      } else if (k === 'test') {
        state.selectedNodeId = v;
      }
    }
  }

  function writeHash(state) {
    var parts = [];
    var active = [];
    var keys = ['passed', 'failed', 'slow', 'skipped', 'errored'];
    for (var i = 0; i < keys.length; i++) {
      if (state.statuses[keys[i]]) { active.push(keys[i]); }
    }
    parts.push('status=' + encodeURIComponent(active.join(',')));
    if (state.search) { parts.push('q=' + encodeURIComponent(state.search)); }
    if (state.marker) { parts.push('marker=' + encodeURIComponent(state.marker)); }
    if (state.slowerThanMs !== null) { parts.push('slow=' + encodeURIComponent(String(state.slowerThanMs))); }
    if (state.selectedNodeId) { parts.push('test=' + encodeURIComponent(state.selectedNodeId)); }
    var target = '#' + parts.join('&');
    if (window.location.hash !== target) {
      window.history.replaceState(null, '', target);
    }
  }

  // -------------------------------------------------------------------
  // Header + toolbar bootstrap.
  // -------------------------------------------------------------------
  function populateHeader(run) {
    var t = run.totals || {};
    populateProjectLabel(run);
    populateHeroSentence(t);
    populateProportionBar(t);
    populateSubLine(run, t);
    populateAboutPanel(run);
    wireAboutPopover();
  }

  function populateProjectLabel(run) {
    var host = document.querySelector('[data-field="project_name"]');
    if (!host) { return; }
    var name = run && run.project_name;
    if (!name) { host.hidden = true; return; }
    host.textContent = String(name);
    host.hidden = false;
  }

  function populateHeroSentence(totals) {
    var host = document.querySelector('[data-field="title"]');
    if (!host) { return; }
    clear(host);
    var total = totals.total || 0;
    var passed = totals.passed || 0;
    var failed = totals.failed || 0;
    var errored = totals.errored || 0;
    var slow = totals.slow || 0;
    // Hero sentence picks the worst-outcome framing so the user sees
    // what actually matters. Failures > errors > slow > pass.
    var outcome, primary, verb;
    if (failed > 0) {
      outcome = 'fail'; primary = failed; verb = 'failed';
    } else if (errored > 0) {
      outcome = 'errored'; primary = errored; verb = 'errored';
    } else if (slow > 0) {
      outcome = 'slow'; primary = slow; verb = 'slow, the rest passed';
    } else {
      outcome = 'pass'; primary = passed; verb = 'passed';
    }
    host.setAttribute('data-outcome', outcome);

    if (outcome === 'pass') {
      var strong = el('strong', { 'data-field': 'passed-count' }, String(passed));
      host.appendChild(strong);
      host.appendChild(document.createTextNode(' of '));
      host.appendChild(el('strong', { 'data-field': 'total-count' }, String(total)));
      host.appendChild(document.createTextNode(' tests passed'));
    } else if (outcome === 'slow') {
      host.appendChild(el('strong', { 'data-field': 'slow-count' }, String(slow)));
      host.appendChild(document.createTextNode(' of '));
      host.appendChild(el('strong', { 'data-field': 'total-count' }, String(total)));
      host.appendChild(document.createTextNode(' tests slow'));
    } else {
      host.appendChild(el('strong', { 'data-field': 'failed-count' }, String(primary)));
      host.appendChild(document.createTextNode(' of '));
      host.appendChild(el('strong', { 'data-field': 'total-count' }, String(total)));
      host.appendChild(document.createTextNode(' tests ' + verb));
    }
  }

  function populateProportionBar(totals) {
    var bar = document.querySelector('.hr-proportion');
    if (!bar) { return; }
    clear(bar);
    var total = totals.total || 0;
    if (total === 0) { return; }
    // Segment order renders left-to-right; put pass first so the bar
    // starts green. Zero-count segments are skipped entirely.
    var segs = [
      { key: 'passed', count: totals.passed || 0 },
      { key: 'slow', count: totals.slow || 0 },
      { key: 'failed', count: totals.failed || 0 },
      { key: 'errored', count: totals.errored || 0 },
      { key: 'skipped', count: totals.skipped || 0 }
    ];
    segs.forEach(function (s) {
      if (s.count <= 0) { return; }
      var node = el('span', {
        'data-seg': s.key,
        'title': s.count + ' ' + s.key
      });
      node.style.width = ((s.count / total) * 100) + '%';
      bar.appendChild(node);
    });
  }

  function populateSubLine(run, totals) {
    setSubField('total', (totals.total || 0) + ' tests');
    setNonZero('passed', totals.passed, '\u2713', 'passed');
    setNonZero('failed', totals.failed, '\u2715', 'failed');
    setNonZero('slow', totals.slow, '\u26A0', 'slow');
    setNonZero('errored', totals.errored, '\u2715', 'errored');
    setNonZero('skipped', totals.skipped, '\u2013', 'skipped');

    setSubField('duration_ms', formatDuration(run.duration_ms));

    var latencies = collectHandleLatencies(data.tests || []);
    var p50 = percentile(latencies, 50);
    var p95 = percentile(latencies, 95);
    var p99 = percentile(latencies, 99);
    setSubField('p50', p50 === null ? 'p50 \u2014' : 'p50 ' + p50.toFixed(0) + 'ms');
    setSubField('p95', p95 === null ? 'p95 \u2014' : 'p95 ' + p95.toFixed(0) + 'ms');
    setSubField('p99', p99 === null ? 'p99 \u2014' : 'p99 ' + p99.toFixed(0) + 'ms');

    setSubField('started_at_friendly', formatFriendlyDate(run.started_at));
  }

  function setNonZero(key, count, icon, label) {
    var n = document.querySelector('[data-field="' + key + '"]');
    if (!n) { return; }
    if (!count || count <= 0) {
      n.hidden = true;
      n.textContent = '';
      return;
    }
    n.hidden = false;
    n.textContent = icon + ' ' + count + ' ' + label;
  }

  function setSubField(name, value) {
    var n = document.querySelector('[data-field="' + name + '"]');
    if (n) { n.textContent = String(value); }
  }

  function populateAboutPanel(run) {
    setAboutDd('started_at', run.started_at);
    setAboutDd('transport', run.transport);
    setAboutDd('allowlist_path', run.allowlist_path);
    setAboutDd('git_sha', run.git_sha);
    setAboutDd('git_branch', run.git_branch);
    setAboutDd('environment', run.environment);
    setAboutDd('hostname', run.hostname);
    setAboutDd('python_version', run.python_version);
    setAboutDd('harness_version', run.harness_version);
    setAboutDd('reporter_version', run.reporter_version);
  }

  function setAboutDd(field, value) {
    var dd = document.querySelector('.hr-about-panel dd[data-field="' + field + '"]');
    if (!dd) { return; }
    if (value === null || value === undefined || value === '' || value === 'unknown') {
      dd.hidden = true;
      if (dd.previousElementSibling) { dd.previousElementSibling.hidden = true; }
      return;
    }
    dd.textContent = String(value);
    dd.hidden = false;
    if (dd.previousElementSibling) { dd.previousElementSibling.hidden = false; }
  }

  function wireAboutPopover() {
    var toggle = document.querySelector('.hr-about-toggle');
    var panel = document.getElementById('hr-about-panel');
    if (!toggle || !panel) { return; }

    function setOpen(open) {
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      panel.hidden = !open;
    }

    toggle.addEventListener('click', function (ev) {
      ev.stopPropagation();
      var open = toggle.getAttribute('aria-expanded') === 'true';
      setOpen(!open);
    });

    document.addEventListener('click', function (ev) {
      if (toggle.getAttribute('aria-expanded') !== 'true') { return; }
      if (panel.contains(ev.target) || toggle.contains(ev.target)) { return; }
      setOpen(false);
    });

    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
        setOpen(false);
        toggle.focus();
      }
    });
  }

  function formatDuration(ms) {
    if (typeof ms !== 'number') { return '\u2014'; }
    if (ms < 1000) { return ms.toFixed(0) + 'ms'; }
    return (ms / 1000).toFixed(2) + 's';
  }

  // "2026-04-17T09:47:16.667+00:00" → "17 Apr 09:47". Degrades to the
  // raw input if the Date constructor rejects it.
  function formatFriendlyDate(iso) {
    if (!iso) { return ''; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return iso; }
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var dd = String(d.getDate());
    var mm = months[d.getMonth()];
    var hh = String(d.getHours()).padStart(2, '0');
    var mi = String(d.getMinutes()).padStart(2, '0');
    return dd + ' ' + mm + ' ' + hh + ':' + mi;
  }

  function populateMarkerDropdown() {
    var select = document.querySelector('[data-filter="markers"]');
    if (!select) { return; }
    var seen = {};
    (data.tests || []).forEach(function (t) {
      (t.markers || []).forEach(function (m) { seen[m] = true; });
    });
    Object.keys(seen).sort().forEach(function (name) {
      select.appendChild(el('option', { value: name }, name));
    });
  }

  function populateIncompleteWorkers(run) {
    var banner = document.querySelector('.hr-incomplete-workers');
    var incomplete = (run && run.xdist && run.xdist.incomplete_workers) || [];
    if (!banner) { return; }
    if (incomplete.length === 0) {
      banner.setAttribute('data-visible', 'false');
      return;
    }
    banner.textContent = 'Warning: ' + incomplete.length + ' xdist worker(s) did not flush a partial: ' + incomplete.join(', ');
    banner.setAttribute('data-visible', 'true');
  }

  // -------------------------------------------------------------------
  // Tree (left nav) — tests grouped by file.
  // -------------------------------------------------------------------
  function buildTree(state) {
    var tree = document.querySelector('.hr-tree');
    if (!tree) { return; }
    clear(tree);

    var byFile = {};
    (data.tests || []).forEach(function (t) {
      var f = t.file || '(unknown)';
      if (!byFile[f]) { byFile[f] = []; }
      byFile[f].push(t);
    });
    var files = Object.keys(byFile).sort();

    files.forEach(function (file) {
      var tests = byFile[file];
      var counts = fileCounts(tests);
      // Smart default: a file that contains any failing / slow / errored
      // test auto-expands; a file where every test passes or is skipped
      // starts collapsed. One click toggles.
      var shouldExpand = counts.fail + counts.slow + counts.errored > 0;
      var fileNode = el('div', {
        'class': 'hr-file',
        'role': 'treeitem',
        'aria-expanded': shouldExpand ? 'true' : 'false'
      });
      var header = el('div', { 'class': 'hr-file-header', tabindex: '0' });
      // Chevron rotates via CSS on `aria-expanded="false"`.
      header.appendChild(el('span', { 'class': 'hr-caret' }, '\u25BE'));
      var slashIdx = file.lastIndexOf('/');
      var fileDir = slashIdx >= 0 ? file.slice(0, slashIdx + 1) : '';
      var fileBase = slashIdx >= 0 ? file.slice(slashIdx + 1) : file;
      var nameEl = el('span', { 'class': 'hr-file-name', title: file });
      if (fileDir) {
        nameEl.appendChild(el('span', { 'class': 'hr-file-dir' }, fileDir));
      }
      nameEl.appendChild(el('span', { 'class': 'hr-file-basename' }, fileBase));
      header.appendChild(nameEl);
      header.appendChild(el('span', { 'class': 'hr-file-count' }, tests.length + ' test' + (tests.length === 1 ? '' : 's')));
      if (counts.fail + counts.errored > 0) {
        header.appendChild(el('span', {
          'class': 'hr-file-fail-count',
          'title': counts.fail + ' failing, ' + counts.errored + ' errored'
        }, String(counts.fail + counts.errored)));
      } else if (counts.slow > 0) {
        header.appendChild(el('span', {
          'class': 'hr-file-fail-count',
          'data-kind': 'slow',
          'title': counts.slow + ' slow'
        }, String(counts.slow)));
      }
      fileNode.appendChild(header);
      var testList = el('div', { 'class': 'hr-tests', role: 'group' });
      tests.forEach(function (t) {
        testList.appendChild(buildTestRow(t, state));
      });
      fileNode.appendChild(testList);
      header.addEventListener('click', function () {
        var expanded = fileNode.getAttribute('aria-expanded') === 'true';
        fileNode.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      });
      header.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          header.click();
        }
      });
      tree.appendChild(fileNode);
    });
  }

  function fileCounts(tests) {
    var c = { pass: 0, fail: 0, slow: 0, skipped: 0, errored: 0 };
    for (var i = 0; i < tests.length; i++) {
      var o = tests[i].outcome;
      if (o === 'passed' || o === 'pass') { c.pass += 1; }
      else if (o === 'failed' || o === 'fail') { c.fail += 1; }
      else if (o === 'slow') { c.slow += 1; }
      else if (o === 'skipped') { c.skipped += 1; }
      else if (o === 'errored') { c.errored += 1; }
    }
    return c;
  }

  function setAllFilesExpanded(expanded) {
    var nodes = document.querySelectorAll('.hr-file');
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }
  }

  function buildTestRow(test, state) {
    var row = el('div', {
      'class': 'hr-test',
      'data-nodeid': test.nodeid,
      'data-outcome': test.outcome,
      'data-markers': (test.markers || []).join(','),
      'role': 'treeitem',
      tabindex: '0'
    });
    var badge = el('span', { 'class': 'hr-badge', 'data-outcome': test.outcome }, outcomeLetter(test.outcome));
    row.appendChild(badge);
    row.appendChild(el('span', { 'class': 'hr-test-name' }, test.name || test.nodeid));
    row.appendChild(el('span', { 'class': 'hr-test-duration' }, formatDuration(test.duration_ms)));
    row.addEventListener('click', function () {
      state.selectedNodeId = test.nodeid;
      apply(state);
    });
    row.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        state.selectedNodeId = test.nodeid;
        apply(state);
      }
    });
    return row;
  }

  function outcomeLetter(o) {
    // Icon + letter (colour is never the sole signal — PRD-007 §4 a11y).
    switch (o) {
      case 'passed': case 'pass': return '\u2713';  // check
      case 'failed': case 'fail': return '\u2715';  // x
      case 'slow': return '\u26A0';                  // warning triangle
      case 'skipped': return '\u2013';               // en-dash
      case 'errored': return '\u2298';               // circled slash
      case 'timeout': return '\u29D6';               // hourglass/crossed diamond
      default: return '\u2022';                       // bullet
    }
  }

  // User-facing label for a timeline action. `published` is outbound;
  // `received` is the inbound callback entry; `matched`/`mismatched` are
  // the matcher verdict for an expect. `deadline` → `timed out` for
  // symmetry with the TIMEOUT handle outcome. `correlation_skipped` is
  // a diagnostic event the user rarely needs to think about. `replied`
  // / `reply_failed` are PRD-008 reply lifecycle events.
  function actionLabel(action) {
    if (action === 'deadline') { return 'timed out'; }
    if (action === 'correlation_skipped') { return 'other scope'; }
    if (action === 'received') { return 'received'; }
    if (action === 'replied') { return 'replied'; }
    if (action === 'reply_failed') { return 'reply failed'; }
    return action;
  }

  // Directional arrow. Published = outbound (→). Received / matched /
  // mismatched / deadline / correlation_skipped all concern the
  // incoming stream on that topic (←). Replied observes then emits, so
  // it reads as both-ways (⇄). Reply-failed observed but never emitted
  // (←).
  function directionArrow(action) {
    if (action === 'published') { return '\u2192'; }  // ->
    if (action === 'replied') { return '\u21c4'; }  // <->
    if (action === 'received' || action === 'matched' ||
        action === 'mismatched' || action === 'deadline' ||
        action === 'correlation_skipped' || action === 'reply_failed') {
      return '\u2190';  // <-
    }
    return '';
  }

  // -------------------------------------------------------------------
  // Detail pane (right) — selected test drill-down.
  // -------------------------------------------------------------------
  function renderDetail(state) {
    var pane = document.querySelector('.hr-detail');
    if (!pane) { return; }
    clear(pane);
    var test = findTest(state.selectedNodeId);
    if (!test) {
      pane.appendChild(el('div', { 'class': 'hr-empty' }, 'Select a test from the left to see its details.'));
      return;
    }
    pane.setAttribute('data-selected-nodeid', test.nodeid);

    pane.appendChild(el('h2', { 'data-field': 'detail-name' }, test.name || test.nodeid));
    pane.appendChild(el('div', { 'class': 'hr-nodeid', 'data-field': 'detail-nodeid' }, test.nodeid));

    if (test.traceback) {
      pane.appendChild(el('pre', { 'class': 'hr-traceback' }, test.traceback));
    }

    // Design C: when a test has exactly one scenario, strip the
    // scenario-level card chrome so the whole detail pane reads as one
    // continuous document. Multi-scenario tests keep scenario cards to
    // preserve structural honesty.
    var scenarios = test.scenarios || [];
    var solo = scenarios.length === 1;
    scenarios.forEach(function (sc) {
      pane.appendChild(renderScenario(sc, test.outcome, solo));
    });

    if (!test.scenarios || test.scenarios.length === 0) {
      var noteText = test.outcome === 'skipped'
        ? ('Skipped: ' + (test.skip_reason || 'no reason recorded'))
        : 'No scenario recorded for this test.';
      pane.appendChild(el('div', { 'class': 'hr-empty' }, noteText));
    }

    if (test.stdout || test.stderr || test.log) {
      pane.appendChild(renderCaptures(test));
    }
  }

  function renderScenario(scenario, testOutcome, solo) {
    var expandInitial = solo || scenario.outcome !== 'pass';
    var node = el('div', {
      'class': 'hr-scenario',
      'data-scenario-name': scenario.name,
      'data-outcome': scenario.outcome,
      'data-solo': solo ? 'true' : 'false',
      'aria-expanded': expandInitial ? 'true' : 'false'
    });
    var header = el('div', { 'class': 'hr-scenario-header', tabindex: '0' });
    header.appendChild(el('span', { 'class': 'hr-badge', 'data-outcome': scenario.outcome }, outcomeLetter(scenario.outcome)));
    header.appendChild(el('span', { 'class': 'hr-scenario-name' }, scenario.name));
    var meta = el('span', { 'class': 'hr-scenario-meta' });
    meta.appendChild(el('span', {}, formatDuration(scenario.duration_ms)));
    var expectCount = (scenario.handles || []).length;
    meta.appendChild(el('span', {}, expectCount + ' expectation' + (expectCount === 1 ? '' : 's')));
    var replyCount = (scenario.replies || []).length;
    if (replyCount) {
      meta.appendChild(el('span', {}, replyCount + ' repl' + (replyCount === 1 ? 'y' : 'ies')));
    }
    var eventCount = (scenario.timeline || []).length;
    meta.appendChild(el('span', {}, eventCount + ' event' + (eventCount === 1 ? '' : 's')));
    if (scenario.timeline_dropped) {
      meta.appendChild(el('span', {}, scenario.timeline_dropped + ' dropped'));
    }
    if (!scenario.completed_normally) {
      meta.appendChild(el('span', { 'data-field': 'completed-flag' }, 'partial'));
    }
    header.appendChild(meta);
    node.appendChild(header);

    var body = el('div', { 'class': 'hr-scenario-body' });
    (scenario.handles || []).forEach(function (h) {
      body.appendChild(renderHandle(h));
    });
    // Reply reports (PRD-008) — one row per reply registered in the
    // scope. Rendered between handles and the timeline so the reader
    // sees assertions, stand-ins, then the chronology.
    var repliesHost = renderReplies(scenario);
    if (repliesHost) {
      body.appendChild(repliesHost);
    }
    // Timeline is lazy-mounted on first expand (PRD-007 §4 "lazy timeline mount").
    var timelineHost = el('div', { 'class': 'hr-timeline', 'data-timeline-mounted': 'false' });
    body.appendChild(timelineHost);
    if (expandInitial) {
      mountTimeline(timelineHost, scenario);
    }
    node.appendChild(body);

    header.addEventListener('click', function () {
      var expanded = node.getAttribute('aria-expanded') === 'true';
      node.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      if (!expanded && timelineHost.getAttribute('data-timeline-mounted') === 'false') {
        mountTimeline(timelineHost, scenario);
      }
    });
    return node;
  }

  // Render a compact reply-reports block for a scenario. Returns null
  // when the scenario has no replies so the caller can skip adding an
  // empty section. Structure: one row per reply with state badge,
  // trigger → reply topics, count summary, and a flags column for
  // `correlation_overridden` / `builder_error`.
  function renderReplies(scenario) {
    var replies = scenario.replies || [];
    if (replies.length === 0) { return null; }
    var host = el('div', { 'class': 'hr-replies', 'data-field': 'replies' });
    var heading = el('h3', {});
    heading.appendChild(document.createTextNode(
      'Replies (' + replies.length + ')'
    ));
    host.appendChild(heading);
    replies.forEach(function (r) {
      var row = el('div', {
        'class': 'hr-reply-row',
        'data-state': r.state,
        'data-trigger-topic': r.trigger_topic,
        'data-reply-topic': r.reply_topic
      });
      row.appendChild(el('span', {
        'class': 'hr-reply-state',
        'data-state': r.state
      }, replyStateLabel(r.state)));
      var topics = el('span', { 'class': 'hr-reply-topics' });
      topics.appendChild(document.createTextNode(r.trigger_topic));
      topics.appendChild(el('span', { 'class': 'hr-reply-arrow' }, '\u2192'));
      topics.appendChild(document.createTextNode(r.reply_topic));
      row.appendChild(topics);
      var counts = el('span', { 'class': 'hr-reply-counts' },
        r.match_count + ' match / ' + r.candidate_count + ' candidate' +
        (r.candidate_count === 1 ? '' : 's'));
      row.appendChild(counts);
      var flags = el('span', { 'class': 'hr-reply-flags' });
      var flagParts = [];
      if (r.builder_error) { flagParts.push(r.builder_error); }
      if (r.correlation_overridden) { flagParts.push('correlation overridden'); }
      flags.appendChild(document.createTextNode(flagParts.join(' · ')));
      row.appendChild(flags);
      host.appendChild(row);
    });
    return host;
  }

  // Short user-facing label for a reply report state. The enum values
  // are wire-stable (`armed_no_match`, `reply_failed`, ...); this maps
  // them to the two-word label the UI shows.
  function replyStateLabel(state) {
    if (state === 'replied') { return 'replied'; }
    if (state === 'reply_failed') { return 'reply failed'; }
    if (state === 'armed_no_match') { return 'no match'; }
    if (state === 'armed_matcher_rejected') { return 'rejected'; }
    return state;
  }

  function mountTimeline(host, scenario) {
    clear(host);
    host.setAttribute('data-timeline-mounted', 'true');
    var entries = scenario.timeline || [];
    if (entries.length === 0) {
      return; // no timeline on passing scenarios without replies
    }

    var maxOffset = 0;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].offset_ms > maxOffset) { maxOffset = entries[i].offset_ms; }
    }
    if (maxOffset <= 0) { maxOffset = 1; }

    var heading = el('h3', {});
    heading.appendChild(document.createTextNode('Message timeline'));
    heading.appendChild(el('small', {}, formatOffset(maxOffset) + ' total'));
    host.appendChild(heading);

    // Clock-source caveat. Offsets come from `asyncio.get_running_loop().time()`
    // — per-process monotonic. Under pytest-xdist every worker has its own
    // epoch, so a waterfall only makes sense *within* one scope on one
    // worker. Warn the reader whenever the run used xdist so they don't
    // try to compare offsets across workers.
    if (data && data.run && data.run.xdist && data.run.xdist.workers) {
      var note = el('div', {
        'class': 'hr-empty',
        'data-field': 'timeline-clock-note',
        'style': 'text-align: left; padding: 0 0 var(--hr-space-2) 0; font-style: normal;'
      });
      note.textContent = 'offsets are monotonic within this scope only — not comparable across xdist workers';
      host.appendChild(note);
    }

    var wrap = el('div', { 'class': 'hr-timeline-wrap' });

    // Jaeger-style waterfall. Each event becomes a row whose depth in
    // the causal tree determines indentation (initial publish = 0;
    // matched / replied children = parent depth + 1). The bar's
    // horizontal position is proportional to `offset_ms / maxOffset`
    // and its width is the hop's propagation latency. Reading top to
    // bottom, indenting right, shows the chain.
    wrap.appendChild(renderWaterfall(entries, maxOffset));

    host.appendChild(wrap);

    if (scenario.timeline_dropped) {
      host.appendChild(el('div', {
        'class': 'hr-empty',
        'data-field': 'timeline-dropped-note'
      }, '\u2026 ' + scenario.timeline_dropped + ' earliest entries dropped (buffer cap)'));
    }
  }

  // -------------------------------------------------------------------
  // Waterfall — Jaeger-style trace view of the scenario's event stream.
  //
  // The server only emits `offset_ms` + `topic` + `action` per event;
  // the causal tree (which event triggered which) is reconstructed
  // client-side by tracking "emitters" per topic. An emitter is any
  // event that put a message on a topic — `published` (test-initiated),
  // or `replied` (whose `detail` names the reply topic). A
  // subsequent event on that topic has the most recent emitter as its
  // parent, and renders one depth deeper.
  // -------------------------------------------------------------------

  function buildWaterfall(entries) {
    // Two parent maps:
    //  - `emitters`: PUBLISHED + REPLIED events. These put a message on
    //    a topic. Their children are RECEIVED events on that topic; the
    //    bar from emitter → RECEIVED is transport propagation.
    //  - `receivers`: RECEIVED events. These are the moment a subscriber
    //    observed the message. Their children are MATCHED / MISMATCHED /
    //    REPLIED / REPLY_FAILED from the same subscriber; that bar is
    //    matcher + builder + publish-enqueue time.
    // Callbacks are run to completion synchronously in both MockTransport
    // and NatsTransport's per-subscription task, so each RECEIVED is
    // contiguous with the MATCHED/REPLIED it spawned in the timeline
    // array. Pairing "most recent RECEIVED on same topic before this
    // MATCHED/REPLIED" is therefore correct.
    var nodes = [];
    var emitters = {};
    var receivers = {};

    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      var node = {
        id: i,
        topic: e.topic,
        action: e.action,
        offsetMs: (typeof e.offset_ms === 'number') ? e.offset_ms : 0,
        detail: e.detail || '',
        wallClock: e.wall_clock || '',
        parentId: null,
        depth: 0,
        startMs: (typeof e.offset_ms === 'number') ? e.offset_ms : 0,
        endMs: (typeof e.offset_ms === 'number') ? e.offset_ms : 0
      };

      var action = e.action;
      if (action === 'published') {
        // Test-initiated outbound publish — root of its subtree.
        registerNode(emitters, e.topic, node);
      } else if (action === 'received') {
        // Subscriber saw a message. Parent = emitter on the same topic.
        // Bar from emitter.endMs → this.endMs is transport propagation.
        var em = mostRecentNode(emitters, e.topic, node.offsetMs);
        if (em) {
          node.parentId = em.id;
          node.depth = em.depth + 1;
          node.startMs = em.offsetMs;
        }
        registerNode(receivers, e.topic, node);
      } else if (action === 'matched' || action === 'mismatched') {
        // Expect subscriber's verdict. Parent = that subscriber's
        // preceding RECEIVED on the same topic; bar is matcher time.
        var rcv = mostRecentNode(receivers, e.topic, node.offsetMs);
        if (rcv) {
          node.parentId = rcv.id;
          node.depth = rcv.depth + 1;
          node.startMs = rcv.offsetMs;
        } else {
          // No RECEIVED ahead — fall back to the emitter (legacy
          // scenarios without RECEIVED events).
          var emFallback = mostRecentNode(emitters, e.topic, node.offsetMs);
          if (emFallback) {
            node.parentId = emFallback.id;
            node.depth = emFallback.depth + 1;
            node.startMs = emFallback.offsetMs;
          }
        }
      } else if (action === 'deadline' ||
                 action === 'correlation_skipped') {
        // Deadline / skipped — parent = emitter if any (DEADLINE may
        // arrive on a topic with no emitter in-scope).
        var emDead = mostRecentNode(emitters, e.topic, node.offsetMs);
        if (emDead) {
          node.parentId = emDead.id;
          node.depth = emDead.depth + 1;
          node.startMs = emDead.offsetMs;
        }
      } else if (action === 'replied' || action === 'reply_failed') {
        // A reply fired. Parent = the RECEIVED that triggered it (same
        // subscriber's callback); bar is matcher + builder + publish.
        var rcv2 = mostRecentNode(receivers, e.topic, node.offsetMs);
        if (rcv2) {
          node.parentId = rcv2.id;
          node.depth = rcv2.depth + 1;
          node.startMs = rcv2.offsetMs;
        } else {
          var em2 = mostRecentNode(emitters, e.topic, node.offsetMs);
          if (em2) {
            node.parentId = em2.id;
            node.depth = em2.depth + 1;
            node.startMs = em2.offsetMs;
          }
        }
        // A REPLIED also emits on its reply topic; register it so the
        // downstream RECEIVED becomes a grandchild.
        if (action === 'replied') {
          var replyTopic = parseReplyTargetTopic(node.detail);
          if (replyTopic) {
            registerNode(emitters, replyTopic, node);
          }
        }
      }
      nodes.push(node);
    }
    return nodes;
  }

  function registerNode(byTopic, topic, node) {
    if (!byTopic[topic]) { byTopic[topic] = []; }
    byTopic[topic].push(node);
  }

  function mostRecentNode(byTopic, topic, atOrBeforeMs) {
    var list = byTopic[topic];
    if (!list || list.length === 0) { return null; }
    for (var i = list.length - 1; i >= 0; i--) {
      if (list[i].offsetMs <= atOrBeforeMs) { return list[i]; }
    }
    // Fall back to the most recent entry — covers clock-skew edge
    // cases where an event's offset is epsilon before its predecessor's.
    return list[list.length - 1];
  }

  function parseReplyTargetTopic(detail) {
    // Detail is formatted by `scenario.py` as `reply=<topic>` or
    // `reply=<topic> error=<ExcClass>`. Parse the topic token only.
    var m = /reply=(\S+)/.exec(detail || '');
    return m ? m[1] : null;
  }

  function niceAxisMax(ms) {
    if (!(ms > 0)) { return 1; }
    var exp = Math.pow(10, Math.floor(Math.log10(ms)));
    var n = ms / exp;
    var nice;
    if (n <= 1) { nice = 1; }
    else if (n <= 2) { nice = 2; }
    else if (n <= 2.5) { nice = 2.5; }
    else if (n <= 5) { nice = 5; }
    else { nice = 10; }
    return nice * exp;
  }

  function renderWaterfall(entries, maxOffset) {
    var root = el('div', {
      'class': 'hr-waterfall',
      role: 'tree',
      'aria-label': 'Message waterfall'
    });

    var axisMax = niceAxisMax(maxOffset);
    root.appendChild(renderWaterfallAxis(axisMax));

    var nodes = buildWaterfall(entries);
    var nodeById = {};
    nodes.forEach(function (n) { nodeById[n.id] = n; });

    var topicShape = computeTopicShape(nodes);

    var rowsById = {};
    nodes.forEach(function (node) {
      var row = renderWaterfallRow(node, axisMax, nodeById, topicShape);
      rowsById[node.id] = row;
      root.appendChild(row);
    });

    // Click-to-expand inline detail. Highlight the ancestor chain
    // (parent, grandparent, …) so the reader sees which earlier event
    // led to this one — equivalent to hovering a span in Jaeger to
    // pick out its parents.
    nodes.forEach(function (node) {
      var row = rowsById[node.id];
      row.addEventListener('click', function () {
        toggleWaterfallDetail(row, node, nodeById);
      });
      row.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          toggleWaterfallDetail(row, node, nodeById);
        }
      });
      row.addEventListener('mouseenter', function () {
        highlightAncestors(node, nodeById, rowsById, true);
      });
      row.addEventListener('mouseleave', function () {
        highlightAncestors(node, nodeById, rowsById, false);
      });
    });

    return root;
  }

  function renderWaterfallAxis(axisMax) {
    var axis = el('div', { 'class': 'hr-waterfall-axis', 'aria-hidden': 'true' });
    axis.appendChild(el('div', { 'class': 'hr-waterfall-axis-label' }, 'event'));

    var track = el('div', { 'class': 'hr-waterfall-axis-track' });
    var fractions = [0, 0.25, 0.5, 0.75, 1];
    fractions.forEach(function (frac) {
      var tick = el('span', {
        'class': 'hr-waterfall-axis-tick',
        style: 'left: ' + (frac * 100) + '%'
      }, formatOffset(axisMax * frac));
      track.appendChild(tick);
    });
    axis.appendChild(track);

    axis.appendChild(el('div', { 'class': 'hr-waterfall-axis-time' }, 'offset'));
    return axis;
  }

  function computeTopicShape(nodes) {
    // Longest prefix and suffix shared by every topic in the scenario,
    // trimmed to dot-segment boundaries so we never split a segment
    // half-dimmed. Anything shorter than 4 chars is not worth dimming.
    if (!nodes || nodes.length < 2) { return { prefix: '', suffix: '' }; }
    var topics = [];
    for (var i = 0; i < nodes.length; i++) {
      var t = nodes[i].topic;
      if (typeof t === 'string' && t.length > 0) { topics.push(t); }
    }
    if (topics.length < 2) { return { prefix: '', suffix: '' }; }

    var prefix = topics[0];
    for (var pi = 1; pi < topics.length; pi++) {
      var maxP = Math.min(prefix.length, topics[pi].length);
      var j = 0;
      while (j < maxP && prefix.charCodeAt(j) === topics[pi].charCodeAt(j)) { j++; }
      prefix = prefix.slice(0, j);
      if (!prefix) { break; }
    }
    var lastDot = prefix.lastIndexOf('.');
    prefix = lastDot >= 0 ? prefix.slice(0, lastDot + 1) : '';

    var suffix = topics[0];
    for (var si = 1; si < topics.length; si++) {
      var a = suffix, b = topics[si];
      var ja = a.length - 1, jb = b.length - 1;
      while (ja >= 0 && jb >= 0 && a.charCodeAt(ja) === b.charCodeAt(jb)) {
        ja--; jb--;
      }
      suffix = a.slice(ja + 1);
      if (!suffix) { break; }
    }
    var firstDot = suffix.indexOf('.');
    suffix = firstDot >= 0 ? suffix.slice(firstDot) : '';

    if (prefix.length < 4) { prefix = ''; }
    if (suffix.length < 4) { suffix = ''; }
    return { prefix: prefix, suffix: suffix };
  }

  function renderTopicCell(topic, shape) {
    // Three-span layout: dim shared prefix, bright variable middle,
    // dim shared suffix. Ellipsis eats the suffix first so the
    // unique middle survives longest in narrow columns.
    var wrap = el('span', {
      'class': 'hr-topic hr-waterfall-topic',
      title: topic
    });
    var prefix = (shape && shape.prefix) || '';
    var suffix = (shape && shape.suffix) || '';
    var middleStart = prefix.length;
    var middleEnd = topic.length - suffix.length;
    if (middleEnd <= middleStart || !topic) {
      wrap.appendChild(document.createTextNode(topic || ''));
      return wrap;
    }
    if (prefix) {
      wrap.appendChild(el('span', { 'class': 'hr-waterfall-topic-shared' }, prefix));
    }
    wrap.appendChild(document.createTextNode(topic.slice(middleStart, middleEnd)));
    if (suffix) {
      wrap.appendChild(el('span', { 'class': 'hr-waterfall-topic-shared' }, suffix));
    }
    return wrap;
  }

  function renderWaterfallRow(node, axisMax, nodeById, topicShape) {
    var row = el('div', {
      'class': 'hr-waterfall-row',
      'data-action': node.action,
      'data-depth': String(node.depth),
      'data-node-id': String(node.id),
      'data-parent-id': node.parentId !== null ? String(node.parentId) : '',
      role: 'treeitem',
      'aria-level': String(node.depth + 1),
      'aria-expanded': 'false',
      tabindex: '0'
    });

    // Label column: tree guides + topic + action verb.
    var label = el('div', { 'class': 'hr-waterfall-label' });
    label.appendChild(renderWaterfallIndent(node, nodeById));
    label.appendChild(renderTopicCell(node.topic, topicShape));
    label.appendChild(el('span', {
      'class': 'hr-waterfall-action',
      'data-action': node.action
    }, actionLabel(node.action)));
    row.appendChild(label);

    // Bar track: the bar spans from parent's offset to this event's
    // offset; that width == propagation latency for this single hop.
    // Dot sits at the event's own offset (right edge of the bar).
    var track = el('div', { 'class': 'hr-waterfall-track' });
    var startPct = clampPct((node.startMs / axisMax) * 100);
    var endPct = clampPct((node.endMs / axisMax) * 100);
    var widthPct = Math.max(0, endPct - startPct);
    var bar = el('div', {
      'class': 'hr-waterfall-bar',
      'data-action': node.action,
      style: 'left: ' + startPct + '%; width: ' + widthPct + '%;'
    });
    track.appendChild(bar);
    var dot = el('div', {
      'class': 'hr-waterfall-dot',
      'data-action': node.action,
      style: 'left: ' + endPct + '%;',
      title: actionLabel(node.action) + ' — ' + node.topic + ' (' + formatOffset(node.endMs) + ')'
    });
    track.appendChild(dot);
    row.appendChild(track);

    // Time column: absolute offset of the event.
    row.appendChild(el('div', { 'class': 'hr-waterfall-time' }, formatOffset(node.endMs)));

    return row;
  }

  function renderWaterfallIndent(node, nodeById) {
    // Guides: one dotted vertical line per ancestor depth, plus a
    // connector glyph on the deepest level. Depth 0 shows the root
    // glyph `•` so every row has a consistent label-column width.
    var container = el('span', {
      'class': 'hr-waterfall-indent',
      'data-depth': String(node.depth)
    });
    for (var d = 0; d < node.depth; d++) {
      container.appendChild(el('span', { 'class': 'hr-waterfall-indent-line' }));
    }
    var connector = node.depth === 0 ? '\u25CF' : '\u2514';
    container.appendChild(el('span', {
      'class': 'hr-waterfall-indent-connector'
    }, connector));
    return container;
  }

  function toggleWaterfallDetail(row, node, nodeById) {
    var existing = row.querySelector(':scope > .hr-waterfall-detail');
    if (existing) {
      existing.remove();
      row.setAttribute('aria-expanded', 'false');
      row.removeAttribute('data-selected');
      return;
    }
    // Collapse any other open detail first — mimics a single-selection
    // Jaeger detail pane.
    var host = row.parentNode;
    if (host) {
      var prior = host.querySelectorAll('.hr-waterfall-row[aria-expanded="true"]');
      prior.forEach(function (r) {
        if (r !== row) {
          r.setAttribute('aria-expanded', 'false');
          r.removeAttribute('data-selected');
          var d = r.querySelector(':scope > .hr-waterfall-detail');
          if (d) { d.remove(); }
        }
      });
    }
    row.setAttribute('aria-expanded', 'true');
    row.setAttribute('data-selected', 'true');
    row.appendChild(renderWaterfallDetail(node, nodeById));
  }

  function renderWaterfallDetail(node, nodeById) {
    var panel = el('div', { 'class': 'hr-waterfall-detail' });
    var pairs = [
      ['time',  formatOffset(node.endMs) + ' from scenario start'],
      ['event', actionLabel(node.action)],
      ['topic', node.topic]
    ];
    var parent = node.parentId !== null ? nodeById[node.parentId] : null;
    if (parent) {
      var delta = Math.max(0, node.endMs - parent.offsetMs);
      pairs.push(['delta',
        '+' + delta.toFixed(1) + 'ms from ' + actionLabel(parent.action) +
        ' on ' + parent.topic
      ]);
    }
    if (node.detail) { pairs.push(['detail', node.detail]); }
    if (node.wallClock) { pairs.push(['wall clock', node.wallClock]); }
    pairs.forEach(function (p) {
      panel.appendChild(el('span', { 'class': 'hr-waterfall-detail-label' }, p[0]));
      panel.appendChild(el('span', { 'class': 'hr-waterfall-detail-value' }, p[1]));
    });
    return panel;
  }

  function highlightAncestors(node, nodeById, rowsById, on) {
    var cur = node;
    while (cur && cur.parentId !== null) {
      var parent = nodeById[cur.parentId];
      if (!parent) { break; }
      var prow = rowsById[parent.id];
      if (prow) {
        if (on) { prow.setAttribute('data-hovered-depth', 'true'); }
        else { prow.removeAttribute('data-hovered-depth'); }
      }
      cur = parent;
    }
  }

  function clampPct(pct) {
    if (!(pct >= 0)) { return 0; }
    if (pct > 100) { return 100; }
    return pct;
  }

  function formatOffset(ms) {
    if (typeof ms !== 'number') { return '\u2014'; }
    return ms.toFixed(1) + 'ms';
  }

  function renderHandle(handle) {
    // Outcome drives the entire body composition. Passed expectations
    // collapse to a one-line note; slow gets a budget-overshoot bar;
    // fail gets a structural diff; timeout shows the absent-message
    // state plus what the matcher was looking for.
    var expanded = handle.outcome !== 'pass';
    var node = el('div', {
      'class': 'hr-handle',
      'data-topic': handle.topic,
      'data-outcome': handle.outcome,
      'aria-expanded': expanded ? 'true' : 'false'
    });
    var header = el('div', { 'class': 'hr-handle-header', tabindex: '0' });
    header.appendChild(el('span', { 'class': 'hr-caret' }, '\u25BE'));
    header.appendChild(el('span', { 'class': 'hr-badge', 'data-outcome': handle.outcome }, outcomeLetter(handle.outcome)));
    // Row layout: chevron + badge + topic + latency. No card, no kind
    // label — the scenario section above establishes context.
    header.appendChild(el('span', { 'class': 'hr-topic' }, handle.topic));
    if (typeof handle.latency_ms === 'number') {
      var latText = handle.latency_ms.toFixed(1) + 'ms';
      if (typeof handle.budget_ms === 'number') {
        latText += ' / ' + handle.budget_ms.toFixed(1) + 'ms';
      }
      header.appendChild(el('span', { 'class': 'hr-handle-latency' }, latText));
    }
    node.appendChild(header);

    var body = el('div', { 'class': 'hr-handle-body' });
    var matcher = el('div', { 'class': 'hr-handle-matcher' });
    matcher.appendChild(document.createTextNode('matches when message '));
    matcher.appendChild(el('code', {}, handle.matcher_description));
    body.appendChild(matcher);

    var outcome = handle.outcome;
    if (outcome === 'pass') {
      body.appendChild(el('div', { 'class': 'hr-pass-note' },
        '\u2713 matched' + (typeof handle.latency_ms === 'number'
          ? ' in ' + handle.latency_ms.toFixed(1) + 'ms' : '')));
    } else if (outcome === 'slow') {
      // The budget bar already carries latency / budget / overshoot. The
      // old `handle.reason` pill was a free-form restatement of the same
      // numbers; the UI now renders only structured fields.
      body.appendChild(renderBudgetBar(handle));
    } else if (outcome === 'timeout') {
      body.appendChild(el('div', { 'class': 'hr-timeout-note' },
        'no message arrived on ' + handle.topic + ' within ' +
        (typeof handle.latency_ms === 'number'
          ? handle.latency_ms.toFixed(0) + 'ms' : 'the timeout')));
      // Show the expected shape so the author knows what would have
      // satisfied the matcher.
      if (handle.expected !== null && handle.expected !== undefined) {
        body.appendChild(renderExpectedOnly(handle.expected));
      }
    } else if (outcome === 'fail') {
      // No reason pill for FAIL — the structural diff below already
      // communicates which path differed and with which value. Keeping
      // the pill was a double-render of the same information (see
      // design-engineer notes 2026-04-17).
      body.appendChild(renderStructuralDiff(handle.expected, handle.actual));
    } else {
      // Fallback: show side-by-side for any unexpected state.
      var diff = el('div', { 'class': 'hr-diff' });
      diff.appendChild(renderDiffPanel('expected', handle.expected));
      diff.appendChild(renderDiffPanel('actual', handle.actual));
      body.appendChild(diff);
    }

    if (handle.truncated) {
      body.appendChild(el('div', { 'class': 'hr-handle-matcher' },
        'Note: payload was truncated to fit report size caps.'));
    }

    node.appendChild(body);
    header.addEventListener('click', function () {
      var isExpanded = node.getAttribute('aria-expanded') === 'true';
      node.setAttribute('aria-expanded', isExpanded ? 'false' : 'true');
    });
    return node;
  }

  function renderBudgetBar(handle) {
    var latency = handle.latency_ms;
    var budget = handle.budget_ms;
    if (typeof latency !== 'number' || typeof budget !== 'number' || budget <= 0) {
      return el('div', {});
    }
    var over = latency - budget;
    var overPct = Math.min(100, Math.max(0, (over / latency) * 100));
    var budgetPct = 100 - overPct;

    var wrap = el('div', { 'class': 'hr-budget' });
    var label = el('div', { 'class': 'hr-budget-label' });
    label.appendChild(document.createTextNode(latency.toFixed(1) + 'ms matched \u2014 budget was ' + budget.toFixed(1) + 'ms, '));
    label.appendChild(el('strong', {}, over.toFixed(1) + 'ms over'));
    wrap.appendChild(label);

    var bar = el('div', { 'class': 'hr-budget-bar' });
    var within = el('span', { 'class': 'hr-budget-bar-fill' });
    within.style.width = budgetPct + '%';
    bar.appendChild(within);
    var overSpan = el('span', { 'class': 'hr-budget-bar-over' });
    overSpan.style.left = budgetPct + '%';
    overSpan.style.width = overPct + '%';
    bar.appendChild(overSpan);
    var marker = el('span', { 'class': 'hr-budget-bar-marker' });
    marker.style.left = budgetPct + '%';
    bar.appendChild(marker);
    wrap.appendChild(bar);
    return wrap;
  }

  function renderExpectedOnly(expected) {
    var wrap = el('div', { 'class': 'hr-structural' });
    wrap.appendChild(el('div', { 'class': 'hr-structural-header' },
      'expected shape \u2014 nothing arrived to compare'));
    var body = el('div', { 'class': 'hr-structural-body' });
    var pre = el('pre', { 'aria-label': 'expected' });
    pre.style.margin = '0';
    appendJson(pre, expected);
    body.appendChild(pre);
    wrap.appendChild(body);
    return wrap;
  }

  function renderStructuralDiff(expected, actual) {
    // Walk expected + actual in parallel, emitting one row per leaf-level
    // comparison. Only differing / missing / extra paths are shown;
    // matches collapse into a summary at the end ("N paths matched").
    // This is the jd-style structural view — kills the "28 identical
    // fields scroll" problem the side-by-side view creates.
    var lines = [];
    var matched = 0;
    diffWalk(expected, actual, '', lines, function () { matched += 1; });

    var wrap = el('div', { 'class': 'hr-structural' });
    var header = el('div', { 'class': 'hr-structural-header' });
    var diffCount = lines.length;
    header.appendChild(el('span', {}, 'actual vs expected'));
    if (diffCount > 0) {
      header.appendChild(el('span', { 'data-field': 'diff-count' }, diffCount + ' differing'));
    }
    if (matched > 0) {
      header.appendChild(el('span', {}, matched + ' matched'));
    }
    wrap.appendChild(header);

    var body = el('div', { 'class': 'hr-structural-body' });
    if (diffCount === 0) {
      body.appendChild(el('div', { 'class': 'hr-empty' }, 'no structural differences'));
    } else {
      lines.forEach(function (l) { body.appendChild(renderDiffLine(l)); });
    }
    wrap.appendChild(body);
    return wrap;
  }

  function diffWalk(expected, actual, path, lines, onMatch) {
    var eType = expected === null ? 'null' : (Array.isArray(expected) ? 'array' : typeof expected);
    var aType = actual === null ? 'null' : (Array.isArray(actual) ? 'array' : typeof actual);

    if (expected === undefined) {
      // Path exists in actual but not in expected — an "extra" we don't
      // complain about (matchers are typically partial-match).
      lines.push({ kind: 'extra', path: path || '<root>', actual: actual });
      return;
    }
    if (actual === undefined) {
      lines.push({ kind: 'missing', path: path || '<root>', expected: expected });
      return;
    }
    if (eType === 'object' && aType === 'object') {
      var keys = Object.keys(expected);
      for (var i = 0; i < keys.length; i++) {
        var k = keys[i];
        var child = path ? path + '.' + k : k;
        diffWalk(expected[k], actual.hasOwnProperty(k) ? actual[k] : undefined,
                 child, lines, onMatch);
      }
      return;
    }
    if (eType === 'array' && aType === 'array') {
      for (var j = 0; j < expected.length; j++) {
        var childIdx = path ? path + '[' + j + ']' : '[' + j + ']';
        diffWalk(expected[j], j < actual.length ? actual[j] : undefined,
                 childIdx, lines, onMatch);
      }
      return;
    }
    if (deepEqual(expected, actual)) {
      onMatch();
      return;
    }
    lines.push({ kind: 'diff', path: path || '<root>', expected: expected, actual: actual });
  }

  function deepEqual(a, b) {
    if (a === b) { return true; }
    if (typeof a !== typeof b) { return false; }
    if (a === null || b === null) { return false; }
    if (Array.isArray(a)) {
      if (!Array.isArray(b) || a.length !== b.length) { return false; }
      for (var i = 0; i < a.length; i++) { if (!deepEqual(a[i], b[i])) { return false; } }
      return true;
    }
    if (typeof a === 'object') {
      var ak = Object.keys(a), bk = Object.keys(b);
      if (ak.length !== bk.length) { return false; }
      for (var j = 0; j < ak.length; j++) {
        if (!Object.prototype.hasOwnProperty.call(b, ak[j])) { return false; }
        if (!deepEqual(a[ak[j]], b[ak[j]])) { return false; }
      }
      return true;
    }
    return false;
  }

  function renderDiffLine(line) {
    var row = el('div', { 'class': 'hr-diff-line', 'data-kind': line.kind });
    row.appendChild(el('span', { 'class': 'hr-diff-path' }, line.path));
    if (line.kind === 'diff') {
      var actualPre = el('span', { 'class': 'hr-diff-value' });
      appendJson(actualPre, line.actual);
      row.appendChild(actualPre);
      row.appendChild(el('span', { 'class': 'hr-diff-arrow' }, '\u2190 expected'));
      var expectedPre = el('span', { 'class': 'hr-diff-expected' });
      appendJson(expectedPre, line.expected);
      row.appendChild(expectedPre);
    } else if (line.kind === 'missing') {
      row.appendChild(el('span', { 'class': 'hr-diff-kind' }, 'missing, expected'));
      var missingPre = el('span', { 'class': 'hr-diff-value' });
      appendJson(missingPre, line.expected);
      row.appendChild(missingPre);
    } else if (line.kind === 'extra') {
      row.appendChild(el('span', { 'class': 'hr-diff-kind' }, 'extra'));
      var extraPre = el('span', { 'class': 'hr-diff-value' });
      appendJson(extraPre, line.actual);
      row.appendChild(extraPre);
    }
    return row;
  }

  function renderDiffPanel(panel, value) {
    var wrap = el('div', { 'class': 'hr-diff-panel', 'data-panel': panel });
    wrap.appendChild(el('h4', {}, panel));
    var pre = el('pre', { 'aria-label': panel });
    if (value === null || value === undefined) {
      pre.textContent = '(none)';
    } else {
      appendJson(pre, value);
    }
    wrap.appendChild(pre);
    return wrap;
  }

  function renderCaptures(test) {
    var host = el('div', { 'class': 'hr-captures' });
    if (test.stdout) { host.appendChild(captureBlock('stdout', test.stdout)); }
    if (test.stderr) { host.appendChild(captureBlock('stderr', test.stderr)); }
    if (test.log) { host.appendChild(captureBlock('log', test.log)); }
    return host;
  }

  function captureBlock(label, content) {
    var d = el('details', { 'data-capture': label });
    d.appendChild(el('summary', {}, label));
    d.appendChild(el('pre', {}, content));
    return d;
  }

  function findTest(nodeid) {
    if (!nodeid) { return null; }
    var tests = data.tests || [];
    for (var i = 0; i < tests.length; i++) {
      if (tests[i].nodeid === nodeid) { return tests[i]; }
    }
    return null;
  }

  // -------------------------------------------------------------------
  // Filters.
  // -------------------------------------------------------------------
  function apply(state) {
    writeHash(state);
    var rows = document.querySelectorAll('.hr-test');
    var visible = 0;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var keep = shouldKeep(row, state);
      row.setAttribute('data-visible', keep ? 'true' : 'false');
      if (keep) { visible += 1; }
      row.setAttribute('data-selected', row.getAttribute('data-nodeid') === state.selectedNodeId ? 'true' : 'false');
    }
    // Files with at least one visible test auto-expand so the matches
    // are actually reachable; files with zero visible tests collapse
    // so the tree stays tidy. Without this, typing into search could
    // filter to hits that sit inside collapsed file groups and the
    // user sees nothing.
    reconcileFileExpansion();
    announce(visible);
    renderDetail(state);
  }

  function reconcileFileExpansion() {
    var files = document.querySelectorAll('.hr-file');
    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      var anyVisible = file.querySelector('.hr-test[data-visible="true"]') !== null;
      file.setAttribute('aria-expanded', anyVisible ? 'true' : 'false');
    }
  }

  function shouldKeep(row, state) {
    var outcome = row.getAttribute('data-outcome');
    if (!state.statuses[outcome]) { return false; }
    if (state.search) {
      var nodeid = row.getAttribute('data-nodeid') || '';
      var name = row.querySelector('.hr-test-name');
      var nameText = name ? name.textContent : '';
      var q = state.search.toLowerCase();
      if (nodeid.toLowerCase().indexOf(q) === -1 && nameText.toLowerCase().indexOf(q) === -1) {
        return false;
      }
    }
    if (state.marker) {
      var markers = (row.getAttribute('data-markers') || '').split(',');
      if (markers.indexOf(state.marker) === -1) { return false; }
    }
    if (state.slowerThanMs !== null) {
      var nodeid2 = row.getAttribute('data-nodeid');
      var test = findTest(nodeid2);
      if (!test || typeof test.duration_ms !== 'number' || test.duration_ms < state.slowerThanMs) {
        return false;
      }
    }
    return true;
  }

  function announce(count) {
    var n = document.querySelector('[aria-live="polite"].hr-announce');
    if (!n) { return; }
    n.textContent = count + ' test' + (count === 1 ? '' : 's') + ' shown';
  }

  // -------------------------------------------------------------------
  // Event wiring.
  // -------------------------------------------------------------------
  function wireToolbar(state) {
    var pills = document.querySelectorAll('.hr-filters button[data-pill]');
    for (var i = 0; i < pills.length; i++) {
      (function (pill) {
        var key = pill.getAttribute('data-pill');
        pill.setAttribute('aria-pressed', state.statuses[key] ? 'true' : 'false');
        pill.addEventListener('click', function () {
          state.statuses[key] = !state.statuses[key];
          pill.setAttribute('aria-pressed', state.statuses[key] ? 'true' : 'false');
          apply(state);
        });
      }(pills[i]));
    }
    var search = document.querySelector('[data-filter="search"]');
    if (search) {
      search.value = state.search;
      search.addEventListener('input', function () {
        state.search = search.value;
        apply(state);
      });
    }
    var marker = document.querySelector('[data-filter="markers"]');
    if (marker) {
      marker.value = state.marker;
      marker.addEventListener('change', function () {
        state.marker = marker.value;
        apply(state);
      });
    }
    var slow = document.querySelector('[data-filter="slower_than_ms"]');
    if (slow) {
      slow.value = state.slowerThanMs !== null ? String(state.slowerThanMs) : '';
      slow.addEventListener('input', function () {
        var v = parseFloat(slow.value);
        state.slowerThanMs = isFinite(v) ? v : null;
        apply(state);
      });
    }

    var bulkExpand = document.querySelector('[data-bulk="expand"]');
    if (bulkExpand) {
      bulkExpand.addEventListener('click', function () { setAllFilesExpanded(true); });
    }
    var bulkCollapse = document.querySelector('[data-bulk="collapse"]');
    if (bulkCollapse) {
      bulkCollapse.addEventListener('click', function () { setAllFilesExpanded(false); });
    }

    document.addEventListener('keydown', function (ev) {
      var inInput = ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT' || ev.target.tagName === 'TEXTAREA');
      if (inInput) { return; }
      if (ev.key === '/') {
        ev.preventDefault();
        if (search) { search.focus(); }
      } else if (ev.key === ']') {
        ev.preventDefault();
        setAllFilesExpanded(true);
      } else if (ev.key === '[') {
        ev.preventDefault();
        setAllFilesExpanded(false);
      }
    });
  }

  // -------------------------------------------------------------------
  // Keyboard-shortcut popover — closes on outside click and Escape,
  // so it doesn't loiter after use.
  // -------------------------------------------------------------------
  function wireKeysMenu() {
    var details = document.querySelector('.hr-keys');
    if (!details) { return; }
    document.addEventListener('click', function (ev) {
      if (!details.open) { return; }
      if (details.contains(ev.target)) { return; }
      details.open = false;
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && details.open) {
        details.open = false;
      }
    });
  }

  // -------------------------------------------------------------------
  // Tree resizer. Drag the rail to resize, double-click or press
  // Enter/Space to collapse, Arrow Left/Right to nudge width, and
  // backslash to toggle collapse from anywhere outside inputs. Width
  // and collapsed state persist to localStorage.
  // -------------------------------------------------------------------
  function wireTreeResizer() {
    var root = document.querySelector('.harness-report');
    var resizer = document.querySelector('.hr-resizer');
    var tree = document.querySelector('.hr-tree');
    if (!root || !resizer || !tree) { return; }

    var MIN_PX = 220;

    function readStorage(key) {
      try { return window.localStorage.getItem(key); } catch (_) { return null; }
    }
    function writeStorage(key, value) {
      try { window.localStorage.setItem(key, value); } catch (_) { /* private mode, quota */ }
    }

    var storedWidth = readStorage('hr-tree-width');
    if (storedWidth) { root.style.setProperty('--hr-tree-width', storedWidth); }
    if (readStorage('hr-tree-collapsed') === 'true') {
      root.setAttribute('data-tree-collapsed', 'true');
    }

    function maxWidthPx() { return Math.max(MIN_PX + 40, window.innerWidth * 0.6); }

    function setWidthPx(px) {
      var clamped = Math.min(Math.max(MIN_PX, Math.round(px)), maxWidthPx());
      var value = clamped + 'px';
      root.style.setProperty('--hr-tree-width', value);
      writeStorage('hr-tree-width', value);
      resizer.setAttribute('aria-valuenow', String(clamped));
    }

    function isCollapsed() {
      return root.getAttribute('data-tree-collapsed') === 'true';
    }

    function setCollapsed(flag) {
      if (flag) { root.setAttribute('data-tree-collapsed', 'true'); }
      else { root.removeAttribute('data-tree-collapsed'); }
      writeStorage('hr-tree-collapsed', flag ? 'true' : 'false');
      resizer.setAttribute('aria-expanded', flag ? 'false' : 'true');
    }

    resizer.setAttribute('aria-valuemin', String(MIN_PX));
    resizer.setAttribute('aria-expanded', isCollapsed() ? 'false' : 'true');

    var dragging = false;
    var dragStartX = 0;
    var dragStartWidth = 0;
    var didDrag = false;

    resizer.addEventListener('mousedown', function (ev) {
      if (ev.button !== 0) { return; }
      if (isCollapsed()) {
        setCollapsed(false);
        ev.preventDefault();
        return;
      }
      dragging = true;
      didDrag = false;
      dragStartX = ev.clientX;
      dragStartWidth = tree.getBoundingClientRect().width;
      resizer.setAttribute('data-dragging', 'true');
      document.body.style.cursor = 'col-resize';
      ev.preventDefault();
    });

    document.addEventListener('mousemove', function (ev) {
      if (!dragging) { return; }
      var dx = ev.clientX - dragStartX;
      if (Math.abs(dx) > 2) { didDrag = true; }
      setWidthPx(dragStartWidth + dx);
    });

    document.addEventListener('mouseup', function () {
      if (!dragging) { return; }
      dragging = false;
      resizer.removeAttribute('data-dragging');
      document.body.style.cursor = '';
    });

    resizer.addEventListener('dblclick', function () {
      setCollapsed(!isCollapsed());
    });

    resizer.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowLeft') {
        ev.preventDefault();
        if (isCollapsed()) { return; }
        setWidthPx(tree.getBoundingClientRect().width - 16);
      } else if (ev.key === 'ArrowRight') {
        ev.preventDefault();
        if (isCollapsed()) { setCollapsed(false); return; }
        setWidthPx(tree.getBoundingClientRect().width + 16);
      } else if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        setCollapsed(!isCollapsed());
      }
    });

    document.addEventListener('keydown', function (ev) {
      var inInput = ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT' || ev.target.tagName === 'TEXTAREA');
      if (inInput) { return; }
      if (ev.key === '\\') {
        ev.preventDefault();
        setCollapsed(!isCollapsed());
      }
    });
  }

  // -------------------------------------------------------------------
  // Boot.
  // -------------------------------------------------------------------
  function boot() {
    var state = initialState();
    populateHeader(data.run || {});
    populateMarkerDropdown();
    populateIncompleteWorkers(data.run || {});
    buildTree(state);
    wireToolbar(state);
    wireTreeResizer();
    wireKeysMenu();
    if (!state.selectedNodeId) {
      var firstFailure = (data.tests || []).find(function (t) {
        return t.outcome === 'failed' || t.outcome === 'errored' || t.outcome === 'slow';
      });
      if (firstFailure) { state.selectedNodeId = firstFailure.nodeid; }
    }
    apply(state);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
}());
"""


_HTML_TEMPLATE = (
    "<!DOCTYPE html>\n"
    "<html lang=\"en\">\n"
    "<head>\n"
    "<meta charset=\"utf-8\">\n"
    "<title>Harness Test Report</title>\n"
    "<meta name=\"harness-schema-version\" content=\"1\">\n"
    "<style>" + _CSS + "</style>\n"
    "</head>\n"
    "<body>\n"
    "<div class=\"harness-report\" data-schema-version=\"1\">\n"
    "  <header class=\"hr-header\">\n"
    "    <div class=\"hr-hero-project\" data-field=\"project_name\" hidden></div>\n"
    "    <div class=\"hr-hero\">\n"
    "      <h1 class=\"hr-hero-sentence\" data-field=\"title\"></h1>\n"
    "      <div class=\"hr-hero-credit\">Built by <a href=\"https://clearroute.io/\" target=\"_blank\" rel=\"noopener noreferrer\">ClearRoute</a></div>\n"
    "      <div class=\"hr-hero-right\">\n"
    "        <span class=\"hr-hero-duration\" data-field=\"duration_ms\"></span>\n"
    "        <details class=\"hr-keys\" aria-label=\"keyboard shortcuts\">\n"
    "          <summary aria-label=\"show keyboard shortcuts\"><span aria-hidden=\"true\">\u2328</span> hotkeys</summary>\n"
    "          <div class=\"hr-keys-panel\" role=\"dialog\" aria-label=\"keyboard shortcuts\">\n"
    "            <h4>hotkeys</h4>\n"
    "            <dl>\n"
    "              <dt><kbd>/</kbd></dt>          <dd>focus search</dd>\n"
    "              <dt><kbd>]</kbd></dt>          <dd>expand all files</dd>\n"
    "              <dt><kbd>[</kbd></dt>          <dd>collapse all files</dd>\n"
    "              <dt><kbd>\\</kbd></dt>         <dd>toggle test list</dd>\n"
    "              <dt><kbd>Esc</kbd></dt>        <dd>close panels</dd>\n"
    "            </dl>\n"
    "          </div>\n"
    "        </details>\n"
    "        <div class=\"hr-about\">\n"
    "          <button type=\"button\" class=\"hr-about-toggle\" aria-expanded=\"false\" aria-controls=\"hr-about-panel\" data-field=\"about-toggle\">\n"
    "            <span aria-hidden=\"true\">i</span> about\n"
    "          </button>\n"
    "          <div class=\"hr-about-panel\" id=\"hr-about-panel\" role=\"dialog\" aria-label=\"about this run\" hidden>\n"
    "            <dl>\n"
    "              <dt>started</dt>       <dd data-field=\"started_at\"></dd>\n"
    "              <dt>transport</dt>     <dd data-field=\"transport\"></dd>\n"
    "              <dt>allowlist</dt>     <dd data-field=\"allowlist_path\"></dd>\n"
    "              <dt>git sha</dt>       <dd data-field=\"git_sha\"></dd>\n"
    "              <dt>git branch</dt>    <dd data-field=\"git_branch\"></dd>\n"
    "              <dt>environment</dt>   <dd data-field=\"environment\"></dd>\n"
    "              <dt>hostname</dt>      <dd data-field=\"hostname\"></dd>\n"
    "              <dt>python</dt>        <dd data-field=\"python_version\"></dd>\n"
    "              <dt>harness</dt>       <dd data-field=\"harness_version\"></dd>\n"
    "              <dt>reporter</dt>      <dd data-field=\"reporter_version\"></dd>\n"
    "            </dl>\n"
    "          </div>\n"
    "        </div>\n"
    "      </div>\n"
    "    </div>\n"
    "    <div class=\"hr-proportion\" role=\"img\" aria-label=\"run proportion\" data-field=\"proportion\"></div>\n"
    "    <div class=\"hr-hero-sub\">\n"
    "      <span data-field=\"total\"></span>\n"
    "      <span data-field=\"passed\" hidden></span>\n"
    "      <span data-field=\"failed\" hidden></span>\n"
    "      <span data-field=\"slow\" hidden></span>\n"
    "      <span data-field=\"errored\" hidden></span>\n"
    "      <span data-field=\"skipped\" hidden></span>\n"
    "      <span class=\"hr-sub-sep\" aria-hidden=\"true\">\u00B7</span>\n"
    "      <span data-field=\"p50\"></span>\n"
    "      <span data-field=\"p95\"></span>\n"
    "      <span data-field=\"p99\"></span>\n"
    "      <span class=\"hr-sub-sep\" aria-hidden=\"true\">\u00B7</span>\n"
    "      <span class=\"hr-sub-ts\" data-field=\"started_at_friendly\"></span>\n"
    "    </div>\n"
    "  </header>\n"
    "  <div class=\"hr-incomplete-workers\" data-visible=\"false\"></div>\n"
    "  <div class=\"hr-toolbar\" role=\"toolbar\">\n"
    "    <div class=\"hr-filters\" role=\"group\" aria-label=\"status filters\">\n"
    "      <button type=\"button\" data-pill=\"passed\" aria-pressed=\"false\">passed</button>\n"
    "      <button type=\"button\" data-pill=\"failed\" aria-pressed=\"true\">failed</button>\n"
    "      <button type=\"button\" data-pill=\"slow\" aria-pressed=\"true\">slow</button>\n"
    "      <button type=\"button\" data-pill=\"skipped\" aria-pressed=\"true\">skipped</button>\n"
    "      <button type=\"button\" data-pill=\"errored\" aria-pressed=\"true\">errored</button>\n"
    "    </div>\n"
    "    <label>markers: <select data-filter=\"markers\"><option value=\"\">all</option></select></label>\n"
    "    <label>search: <input type=\"search\" data-filter=\"search\" aria-label=\"search tests\"></label>\n"
    "    <label>slower than: <input type=\"number\" data-filter=\"slower_than_ms\" min=\"0\" aria-label=\"duration threshold\"> ms</label>\n"
    "    <div class=\"hr-bulk\" role=\"group\" aria-label=\"expand/collapse all\">\n"
    "      <button type=\"button\" data-bulk=\"expand\" title=\"Expand all files (])\">expand all</button>\n"
    "      <button type=\"button\" data-bulk=\"collapse\" title=\"Collapse all files ([)\">collapse all</button>\n"
    "    </div>\n"
    "    <div class=\"hr-announce\" aria-live=\"polite\" id=\"filter-announce\"></div>\n"
    "  </div>\n"
    "  <div class=\"hr-body\">\n"
    "    <nav class=\"hr-tree\" role=\"tree\" aria-label=\"test list\"></nav>\n"
    "    <div class=\"hr-resizer\" role=\"separator\" aria-orientation=\"vertical\" aria-label=\"Resize test list — double-click or press Enter to toggle\" tabindex=\"0\"></div>\n"
    "    <section class=\"hr-detail\" aria-live=\"polite\"></section>\n"
    "  </div>\n"
    "</div>\n"
    "<script type=\"application/json\" id=\"harness-results\">__HARNESS_RESULTS_JSON__</script>\n"
    "<script>" + _JS + "</script>\n"
    "</body>\n"
    "</html>\n"
)
