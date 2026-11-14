#!/usr/bin/env bash
# Stop hook: Claude may not end a turn while its Python changes are red.
# Exit 2 blocks the stop and sends stderr back to Claude as the reason.
#
# Scope: ruff runs on the Python files with uncommitted changes, mypy on the whole
# project (a type error usually shows up in a file other than the one edited).
# A clean tree or a turn without Python changes costs nothing.
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
command -v uv >/dev/null 2>&1 || exit 0
command -v git >/dev/null 2>&1 || exit 0

changed=()
while IFS= read -r -d '' file; do
    [ -f "$file" ] && changed+=("$file")
done < <(
    git diff --name-only -z --diff-filter=d HEAD -- '*.py' 2>/dev/null
    git ls-files --others --exclude-standard -z -- '*.py' 2>/dev/null
)
[ "${#changed[@]}" -eq 0 ] && exit 0

# A broken environment (no network for a cold sync, missing lockfile) is nothing Claude
# can fix by editing code. Say so and let the turn end instead of blocking on it.
if ! uv run --quiet ruff --version >/dev/null 2>&1; then
    echo "stop-check: uv environment not usable, lint and type check skipped" >&2
    exit 0
fi

if ! out="$(uv run --quiet ruff check "${changed[@]}" 2>&1)"; then
    printf 'ruff check failed:\n%s\n' "$out" >&2
    exit 2
fi
if ! out="$(uv run --quiet mypy src tests 2>&1)"; then
    printf 'mypy failed:\n%s\n' "$out" >&2
    exit 2
fi
exit 0
