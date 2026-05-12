#!/usr/bin/env bash
# Initialize a new project from this template.
# Replaces PROJECT_NAME placeholders, sets up git, runs initial install.
#
# Usage: ./init-project.sh <project-name> [description]
# Example: ./init-project.sh my-cool-app "A cool app that does cool things"

set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Usage: $0 <project-name> [description]"
    echo "Example: $0 my-cool-app \"A cool app\""
    exit 1
fi

PROJECT_NAME_RAW="$1"
PROJECT_DESC="${2:-A new Python project}"

# Convert to valid Python module name (replace - with _)
PROJECT_NAME_PY="${PROJECT_NAME_RAW//-/_}"

echo "→ Project name: $PROJECT_NAME_RAW"
echo "→ Python module: $PROJECT_NAME_PY"
echo "→ Description: $PROJECT_DESC"
echo ""

# Rename the source directory
if [ -d "src/PROJECT_NAME" ]; then
    mv "src/PROJECT_NAME" "src/$PROJECT_NAME_PY"
    echo "✓ Renamed src/PROJECT_NAME → src/$PROJECT_NAME_PY"
fi

# Replace placeholders in text files (Linux/Mac compatible sed)
echo "→ Replacing placeholders in files..."
find . -type f \
    \( -name "*.toml" -o -name "*.md" -o -name "*.yml" -o -name "*.yaml" -o -name "*.py" -o -name "Makefile" \) \
    -not -path "./.git/*" \
    -not -path "./node_modules/*" \
    -print0 | while IFS= read -r -d '' file; do
    if grep -q "PROJECT_NAME\|PROJECT_DESCRIPTION" "$file" 2>/dev/null; then
        # Use a temp file for cross-platform compatibility
        sed -e "s/PROJECT_NAME/$PROJECT_NAME_PY/g" \
            -e "s|PROJECT_DESCRIPTION|$PROJECT_DESC|g" \
            "$file" > "$file.tmp" && mv "$file.tmp" "$file"
    fi
done
echo "✓ Placeholders replaced"

# Initialize git if not already a repo
if [ ! -d .git ]; then
    git init -q
    echo "✓ Initialized git repository"
fi

# Remove the init script itself (one-time use)
rm -- "$0"
echo "✓ Removed init script (one-time use)"

echo ""
echo "Next steps:"
echo "  1. Fill in docs/ai/CONTEXT.md with project details"
echo "  2. Run: make install"
echo "  3. Run: make check  (verify everything works)"
echo "  4. git add . && git commit -m 'chore: initial commit from python-template'"
