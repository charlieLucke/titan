# titan

Local high-performance RAG system on a single workstation

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
make install
```

This installs all dependencies and registers pre-commit hooks.

## Development

```bash
make dev        # start dev server (define in Makefile)
make test       # run tests with coverage
make test-fast  # run only fast tests
make check      # full quality gate: lint + types + tests
make format     # auto-fix style issues
make help       # list all available commands
```

## Project Structure

```
src/titan/    Source code
tests/               Pytest tests (mirrors src/ layout)
docs/ai/             AI agent context and plans
.github/workflows/   CI configuration
```

## Tooling

| Tool         | Purpose                              |
|--------------|--------------------------------------|
| **uv**       | Package manager + Python installer   |
| **ruff**     | Linter + formatter                   |
| **mypy**     | Static type checker (strict mode)    |
| **pytest**   | Test runner with coverage            |
| **pre-commit** | Git hook runner                    |

All tools run in CI on every push.

## Working with AI Tools

This project uses a structured workflow for AI-assisted coding. Any AI agent (Claude, Gemini, Cursor, Aider, etc.) should read `CLAUDE.md` first — it's mirrored as `AGENTS.md` and `GEMINI.md` for tool compatibility.

Key files for AI context:

- `docs/ai/CONTEXT.md` — stack, conventions, glossary
- `docs/ai/CURRENT_TASK.md` — what's actively being worked on
- `docs/ai/HANDOFF.md` — state for resuming sessions across model switches
- `docs/ai/DECISIONS.md` — log of architectural decisions
- `docs/ai/plans/` — saved plans authored by a planning model (e.g. Opus)

The intended workflow:

1. Architecture and feature plans are authored by a strong reasoning model and saved to `docs/ai/plans/`
2. A faster/cheaper model implements the plans
3. Both reference the shared context in `docs/ai/`
4. State is preserved across sessions via `HANDOFF.md`

## License

TBD
