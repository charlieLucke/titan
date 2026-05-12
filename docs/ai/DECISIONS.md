# Decisions Log

> Architecture Decision Records. Append-only. One entry per significant decision.
> This prevents re-litigating the same questions in every new AI session.

## Format

```
## YYYY-MM-DD: Short title
**Decision:** What we decided
**Reasoning:** Why
**Alternatives considered:** What we rejected and why
**Consequences:** What this implies going forward
```

---

## Initial decisions (template defaults)

## 2026-XX-XX: Use uv as package manager
**Decision:** uv (over pip+venv, poetry, pdm).
**Reasoning:** 10-100x faster than pip; unified tool replacing pip, pip-tools, virtualenv, pyenv; lockfile by default; backed by Astral (same team as ruff).
**Alternatives considered:** Poetry (slower, more config overhead, separate from venv tooling). pip+venv (no lockfile by default, manual workflow).
**Consequences:** All dependency operations go through `uv add` / `uv remove` / `uv sync`. Never edit pyproject.toml dependencies manually.

## 2026-XX-XX: Use ruff for lint and format
**Decision:** ruff replaces black + flake8 + isort + pyupgrade.
**Reasoning:** Single tool, much faster, consistent config, actively maintained.
**Consequences:** Don't add black, flake8, or isort as separate tools.

## 2026-XX-XX: Mypy strict mode
**Decision:** Mypy in strict mode from day one.
**Reasoning:** Strictness is much easier to enforce from the start than retrofit. Catches whole categories of bugs at write-time.
**Consequences:** Every function needs full type hints. `# type: ignore` requires an inline comment explaining why.
