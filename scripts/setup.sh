#!/usr/bin/env bash
# One-shot setup from a fresh git clone.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_DIR}/.venv"
OUTPUT_DIR="${BC_OUTPUT_DIR:-${HOME}/apppilot-results}"

echo "== AppPilot one-shot setup =="

# Python version check
python -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ required'" || {
    echo "Error: Python 3.11 or newer is required."
    exit 1
}

# Create virtual environment
if [ ! -d "${VENV_DIR}" ]; then
    echo "Creating virtual environment at ${VENV_DIR}..."
    python -m venv "${VENV_DIR}"
fi

# Upgrade pip and install the project + dev dependencies
echo "Installing AppPilot dependencies..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -e "${REPO_DIR}[dev]"

# Install Playwright Chromium browser binary
echo "Installing Playwright Chromium..."
"${VENV_DIR}/bin/playwright" install chromium

# Ensure output directory exists
mkdir -p "${OUTPUT_DIR}"
echo "Output directory: ${OUTPUT_DIR}"

# Run the test suite
echo "Running tests..."
"${VENV_DIR}/bin/python" -m pytest tests/ -q

# Register the MCP server if Claude Code is installed and not already registered
if command -v claude >/dev/null 2>&1; then
    if claude mcp get apppilot >/dev/null 2>&1; then
        echo "MCP server 'apppilot' already registered."
    else
        echo "Registering AppPilot MCP server..."
        claude mcp add apppilot "${VENV_DIR}/bin/apppilot-mcp" -e "BC_OUTPUT_DIR=${OUTPUT_DIR}"
        echo "Registered. Restart Claude Code if it is already running."
    fi
else
    echo "Claude CLI not found. To register manually, run:"
    echo "  claude mcp add apppilot ${VENV_DIR}/bin/apppilot-mcp -e BC_OUTPUT_DIR=${OUTPUT_DIR}"
fi

# Optionally link wrappers onto PATH
if [ "${APPPILOT_LINK_WRAPPERS:-}" = "1" ]; then
    echo "Linking wrappers to PATH..."
    if [ "${EUID:-}" -eq 0 ]; then
        TARGET_DIR="${APPPILOT_WRAPPER_DIR:-/usr/local/bin}"
    else
        TARGET_DIR="${APPPILOT_WRAPPER_DIR:-${HOME}/.local/bin}"
    fi
    mkdir -p "${TARGET_DIR}"
    for wrapper in apppilot apppilot-mcp; do
        src="${VENV_DIR}/bin/${wrapper}"
        dst="${TARGET_DIR}/${wrapper}"
        if [ -L "${dst}" ] || [ -e "${dst}" ]; then
            rm -f "${dst}"
        fi
        ln -s "${src}" "${dst}"
        echo "  ${dst} -> ${src}"
    done
    echo "Add ${TARGET_DIR} to your PATH if it is not already:"
    echo "  export PATH=\"${TARGET_DIR}:\${PATH}\""
fi

echo ""
echo "Setup complete."
echo ""
echo "Quick start (set BC_OUTPUT_DIR first):"
echo "  export BC_OUTPUT_DIR=${OUTPUT_DIR}"
echo "  ${VENV_DIR}/bin/python -m apppilot.cli session --headed --open-browser --start-url https://example.com"
echo ""
echo "Or invoke via MCP: mcp__apppilot__apppilot_execute"
