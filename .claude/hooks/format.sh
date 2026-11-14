#!/usr/bin/env bash
# PostToolUse hook: format the Python file Claude just edited.
# Formatting only. `ruff check --fix` is left out on purpose: it would delete an import
# that the next edit is about to use.
file="$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))' 2>/dev/null)"
case "$file" in
    *.py) command -v uv >/dev/null 2>&1 && uv run --quiet ruff format --quiet "$file" >/dev/null 2>&1 ;;
esac
exit 0
