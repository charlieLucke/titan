# Agent Instructions: titan

> Single source for every coding agent. `CLAUDE.md` and `GEMINI.md` only import this file;
> edit here, nowhere else. Keep it short: it is loaded into every session, so it holds only
> what the code and `pyproject.toml` do not already show.

## Read These First

Read on demand, not as a ritual. Open what the task touches:

1. `docs/ai/DECISIONS.md`: before changing a dependency, a module boundary or the data model
2. `docs/ai/ARCHITECTURE.md`: before adding a module or an external service
3. The plan in `docs/ai/plans/`: if the task names one; follow it and flag deviations
4. `docs/ai/CONTEXT.md`: what the project is for, its stack, project-specific conventions,
   known pitfalls and glossary
5. `docs/ai/SYSTEM_LINK.md`: this repo is part of a larger system; read it before changing a
   public interface

## Commands

- `make install`: dependencies and pre-commit hooks
- `make check`: the full gate: format check, lint, types, dependency and duplication audit, tests
- `make format`: auto-format and auto-fix lint
- `make test-fast`: skip tests marked `@pytest.mark.slow` or `@pytest.mark.integration`; mark
  them, or this target skips nothing
- One test: `uv run pytest tests/test_smoke.py::test_name`

## Rules the code does not show

- Dependencies only through `uv add` / `uv remove`; commit `uv.lock` with them. Ask before
  adding one.
- `# type: ignore` and `# noqa` carry an inline reason. Never disable a test, a lint rule
  or a pre-commit hook to get to green.
- Custom exceptions inherit from the project's base exception. `logging`, never `print()`.
- Not enforced by tooling, still the convention: Google-style docstrings on public APIs,
  `from __future__ import annotations` at the top of each module, tests mirror the `src/`
  layout and use pytest fixtures.
- Stay in scope: no refactoring mixed into a feature or fix. Unrelated ideas go to
  `docs/ai/IDEAS.md`.
- A decision that outlives the task (new dependency, boundary, data model, API shape) gets
  an entry in `docs/ai/DECISIONS.md`.
- Commits: `type: subject` with feat, fix, refactor, test, docs or chore; one logical change
  each.

## Stop and ask first

- Two valid designs with a real trade-off, or a plan that contradicts the code
- Anything touching auth, money, concurrency, security or a data migration
- A change to a public interface that another repo or service consumes

## Done means

`make check` is green, and you show the command and its output. In Claude Code a Stop hook
(`.claude/hooks/stop-check.sh`) refuses to end a turn while there are uncommitted Python
changes and ruff fails on them or mypy fails on the project; fix the cause, do not work
around the hook.

## Handing over

Only when the user asks for a handoff or the work moves to another tool or model: overwrite
`docs/ai/HANDOFF.md` using the headings already in that file. Within one tool, resuming the
session is enough.

## Known pitfalls

Recorded in `docs/ai/CONTEXT.md` under "Bekannte Fallstricke". Read that section before
working in an area it names, and append there what cost time once.
