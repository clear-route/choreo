# Contributing to Choreo

Thanks for looking at Choreo. This file captures the rules that keep the codebase honest.

## Before opening a PR

1. Open an issue or a discussion first if the change is more than a typo fix. Architectural changes deserve an ADR — see [docs/adr/README.md](docs/adr/README.md).
2. Read [CLAUDE.md](CLAUDE.md) (project conventions, enforced in review) and [docs/context.md §15](docs/context.md) (global writing style).
3. Run the full suite locally before pushing:
   ```
   pip install -e 'packages/core[test]' -e 'packages/core-reporter[test]'
   pytest
   ```
   E2E tests require the Docker Compose stack in [docker/compose.e2e.yaml](docker/compose.e2e.yaml) and run only under `pytest -m e2e`.

## Test style

- Name tests for **observable behaviour**, not implementation: `test_x_should_y` / `test_x_should_not_y`.
- Assert on observable effects (return values, published messages, raised exceptions). Avoid peeking at private attributes.
- One behaviour per test. Shared setup goes in fixtures; shared shape goes in parametrisation.

See [CLAUDE.md](CLAUDE.md) for detail and examples.

## Design changes

Write an ADR if the change:

- is hard to reverse (connection lifecycle, isolation model, cross-cutting dispatch);
- closes off alternatives a future reader might wonder about;
- introduces a dependency that downstream work relies on;
- has security implications.

Use [docs/adr/template.md](docs/adr/template.md). ADRs are reviewed on the same PR as the code change that implements them. Don't land code that contradicts an Accepted ADR without superseding it.

## Commit hygiene

- Small, focused commits. One logical change per commit.
- Commit messages follow the subject / body pattern. Subject under 70 chars; body explains *why*, not *what*.
- Don't bundle a rename with a refactor with a new feature.

## Chronicle

Chronicle is a separate package (`packages/chronicle/`) with its own FastAPI backend, TimescaleDB persistence, and React frontend. Follow the same test naming conventions as the core library.

### Dev environment setup

```bash
# 1. Start TimescaleDB
docker compose -f docker/compose.chronicle.yaml up -d

# 2. Install the package with test extras
pip install -e 'packages/chronicle[test]'

# 3. Apply database migrations
cd packages/chronicle && alembic upgrade head
```

### Frontend development

Requires Node.js 20. The Vite dev server proxies API calls to the backend on port 8000.

```bash
cd packages/chronicle/frontend
npm install
npm run dev
```

### Running tests

```bash
# Unit and integration tests (no database required)
pytest packages/chronicle/tests/

# End-to-end tests (requires TimescaleDB from the compose stack above)
pytest packages/chronicle/tests/ -m chronicle_db
```

## Security

If you spot a vulnerability, do not open a public issue. See [SECURITY.md](SECURITY.md) for private reporting.

Every PR is scanned by [`pip-audit`](https://github.com/pypa/pip-audit) against the installed-dep surface (all transport extras plus the reporter). The job runs on PRs, on `main`, and on a weekly schedule so CVEs in unchanged deps still surface. Dependabot opens PRs for `pip` and `github-actions` updates weekly; treat security-flagged bumps as priority.

If a PR has to land with a known advisory (e.g. fix not yet released upstream), pass `--ignore-vuln GHSA-xxxx` in the workflow and open a tracking issue on the same day. Waivers without a tracking issue will not pass review.

## Licensing

By contributing, you agree that your contributions are licensed under the Apache License 2.0 — the same licence as the project. See [LICENSE](LICENSE).

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Be decent.
