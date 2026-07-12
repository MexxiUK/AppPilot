# AppPilot

An **AI-first, self-hosted browser executor** — an open alternative to Google Antigravity.

It is designed to be invoked by an LLM from a terminal or through the **Model Context Protocol (MCP)**. You send it a list of browser actions. It drives a real browser, returns structured perception data, and saves every session under a configurable output directory. A live dashboard is available but optional.

## Design goals

- **AI-native / MCP-first**: exposes `apppilot_execute` as the primary MCP tool. The caller LLM decides the actions; the tool only drives the browser.
- **Terminal-native**: every command accepts flags and emits parseable JSON.
- **Action executor, not a hidden agent**: the tool only runs caller-provided actions; the caller LLM owns the reasoning loop.
- **No `.env` files**: configure with `BC_*` environment variables or CLI flags.
- **Headless by default**: runs without opening windows unless you ask.
- **Deterministic output**: structured responses and explicit exit codes.
- **Recorded sessions**: every run is saved under a configurable output directory so an LLM can inspect or replay it later.

## One-shot install from git

```bash
git clone <repo-url> apppilot
cd apppilot
./scripts/setup.sh
```

This creates `.venv`, installs dependencies, installs Playwright Chromium, creates `~/apppilot-results`, runs the test suite, and registers the MCP server with Claude Code if the `claude` CLI is installed.

To use a different output directory:

```bash
BC_OUTPUT_DIR=/path/to/results ./scripts/setup.sh
```

To also link the `apppilot` / `apppilot-mcp` wrappers onto your PATH:

```bash
APPPILOT_LINK_WRAPPERS=1 ./scripts/setup.sh
```

If the Claude CLI is not installed, the script prints the manual `claude mcp add` command.

### Manual install (if you prefer)

```bash
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install chromium
mkdir -p ~/apppilot-results
claude mcp add apppilot .venv/bin/apppilot-mcp -e BC_OUTPUT_DIR=$HOME/apppilot-results
```

## Output directory

All results, reports, and screenshots are saved under the directory specified by `BC_OUTPUT_DIR` or `--output-dir`. There is no default; the app throws an error if no directory is provided.

Structure:

```
<output_dir>/
├── sessions/
│   └── <session_id>.jsonl       # every step, perception, and screenshot paths
├── screenshots/
│   └── <session_id>_step<N>_<before|after>.jpg
└── reports/
    └── <session_id>.json        # high-level summary: status, result, errors
```

## MCP usage (recommended)

Register the server with Claude Code:

```bash
claude mcp add apppilot apppilot-mcp --env-var BC_OUTPUT_DIR=/home/david-lee/apppilot-results
```

Then ask Claude to drive the browser step-by-step:

> Use apppilot_execute to open https://www.google.co.uk, search for "How many different colours of llamas exist?", and report the first result.

Claude will receive the page perception (URL, title, interactive elements, console logs, network summary, page errors) and a screenshot after every action. When the last action is `extract`, no screenshot is returned.

## Terminal usage

```bash
# Execute a caller-provided action list and return the final state
apppilot run \
  --actions '[
    {"type":"goto","url":"https://www.google.co.uk","reason":"Open Google UK"},
    {"type":"wait","duration_ms":1000,"reason":"Let page load"}
  ]' \
  --headed --dashboard \
  --task "Google UK smoke test"

# Quickly inspect a page
apppilot snapshot https://example.com

# Inspect a past run
apppilot inspect <session_id>

# Replay a past run over the dashboard WebSocket
apppilot replay <session_id> --open-browser

# Start only the dashboard server on a random high port
apppilot serve

# Start a persistent session (dashboard + control socket) on random high ports
apppilot session \
  --headed \
  --open-browser \
  --start-url https://www.google.co.uk

# Pump an action batch through the running session
apppilot run_session \
  --session ws://localhost:<control_port>/control \
  --start-url https://www.google.co.uk \
  --actions '[
    {"type":"wait","duration_ms":1000,"reason":"Let page settle"},
    {"type":"screenshot","reason":"Capture state"}
  ]' \
  --task "Google smoke test"

# Reset the running session for a fresh filmstrip
apppilot new_test --session ws://localhost:<control_port>/control
```

## Environment variables

All CLI flags can be set via `BC_*` environment variables:

| Variable | Purpose |
|---|---|
| `BC_OUTPUT_DIR` | Root directory for sessions, screenshots, and reports. Required if not passed via `--output-dir`. |
| `BC_BROWSER` | `chromium`, `firefox`, or `webkit`. Default: `chromium`. |
| `BC_HEADLESS` | `true` or `false`. Default: `true`. |
| `BC_WINDOW_WIDTH` / `BC_WINDOW_HEIGHT` | Browser viewport size. |
| `BC_MAX_STEPS` | Maximum action steps per run. Default: `30`. |

CLI flags override environment variables.

## Tools

| Tool | Purpose |
|---|---|
| `apppilot_execute` | Execute a caller-provided list of browser actions. |
| `apppilot_snapshot` | Load a URL and return a screenshot + page perception. |
| `apppilot_inspect` | Read a saved session summary. |
| `apppilot_replay` | Replay a saved session over the dashboard. |
| `apppilot_run_session` | Send an action batch to a running session. |

Persistent sessions and `new_test` are started via the `apppilot session` / `apppilot new_test` CLI commands; once running, `apppilot_run_session` sends action batches to them.

See `TOOL_SCHEMA.json` for the full machine-readable schema.

## Action reference

Actions are JSON objects sent to `apppilot_execute` or to the CLI `--actions` flag.

| Field | Used by | Description |
|---|---|---|
| `type` | all | `goto`, `click`, `hover`, `type`, `scroll`, `wait`, `screenshot`, `keypress`, `keydown`, `keyup`, `mousedown`, `mouseup`, `drag`, `human_input`, `dismiss_popup`, `extract`, `finish`, `fail`. |
| `element_index` | click, hover, type | Index from the returned interactive element list. Preferred targeting method. |
| `selector` | click, hover, type | CSS selector fallback. |
| `text` | type | Text to type, or text label fallback for targeting. |
| `prompt` | human_input | Message shown to the human operator (terminal/dashboard) before collecting input. |
| `url` | goto | URL to navigate to. |
| `x` / `y` | click, hover, drag, mousedown, mouseup | Absolute coordinates. |
| `end_x` / `end_y` | drag | Drag destination. |
| `key` | keypress, keydown, keyup | Key name, e.g. `Enter`, `ArrowUp`, `w`, `Space`. |
| `direction` | scroll | `up` or `down`. |
| `amount` | scroll | Pixels to scroll. Default: `300`. |
| `duration_ms` | wait | Milliseconds to wait. Default: `1000`. |
| `extract_schema` | extract | JSON schema mapping output keys to CSS selectors. See extraction section below. |
| `reason` | all | Optional note explaining why the action is being taken. |

## Structured extraction

Use the `extract` action to pull structured data from the page without parsing screenshots or large element lists. This is the cheapest way to scrape or summarize a page.

```json
{"type":"extract","extract_schema":{"title":"h1","price":".price","items":{"selector":".product","multiple":true,"fields":{"name":".title","price":".price","rating":".rating"}}},"reason":"Extract product listings"}
```

Schema value formats:

| Format | Example | Meaning |
|---|---|---|
| string selector | `"title": "h1"` | Return the element's trimmed text. |
| object with `selector` | `{"selector": ".price", "attribute": "text"}` | Return a specific attribute. |
| `attribute: "count"` | `{"selector": ".item", "attribute": "count"}` | Return the number of matching elements. |
| `attribute: "exists"` | `{"selector": ".error", "attribute": "exists"}` | Return `true` if at least one element matches. |
| `multiple: true` + `fields` | `{"selector": ".row", "multiple": true, "fields": {"name": "td:first-child"}}` | Return an array of nested objects. |

Supported attributes: `text` (default), `html`, `value`, `href`, `src`, and any DOM attribute via `getAttribute`. The result of the last successful `extract` action is surfaced in the response as `extracted_data` and saved in the session report.

## Human handoff

For CAPTCHAs, 2FA, logins, or any challenge that should not be solved automatically, include a `human_input` action in the action list. The browser pauses, shows the provided `prompt` in the terminal and dashboard, and waits for the user to type a response. The browser itself is never closed during the handoff.

```json
{"type":"human_input","prompt":"Solve the CAPTCHA and press Enter when done.","reason":"CAPTCHA challenge"}
```

## Popups and overlays

The executor detects blocking overlays (modals, cookie banners, sign-in prompts) and reports them in every perception as an `overlay` object. LLMs should check for this field before clicking.

To handle a popup explicitly, use:

```json
{"type":"dismiss_popup","reason":"Close cookie consent banner"}
```

This tries common dismiss buttons ("Reject additional cookies", "Maybe later", "Close", etc.) and fails only if no known dismissable overlay is found.

The executor also auto-dismisses known popups before `click`, `hover`, or `type` actions when no explicit target is provided, so generic flows like `goto` then `type` are less likely to fail because of an overlay.

## Output format

A successful `run` emits:

```json
{
  "type": "execute_complete",
  "session_id": "a1b2c3d4e5f6",
  "task": "Google UK smoke test",
  "status": "success",
  "result": null,
  "step_count": 2,
  "output_dir": "/home/david-lee/apppilot-results",
  "session_path": "/home/david-lee/apppilot-results/sessions/a1b2c3d4e5f6.jsonl",
  "report_path": "/home/david-lee/apppilot-results/reports/a1b2c3d4e5f6.json",
  "screenshots_dir": "/home/david-lee/apppilot-results/screenshots",
  "steps": [...],
  "perception": {
    "url": "https://www.google.co.uk",
    "title": "Google",
    "viewport": {"width": 1280, "height": 720},
    "scroll_position": {"x": 0, "y": 0},
    "elements": [...],
    "console_logs": [...],
    "network_summary": {...},
    "page_errors": [...]
  }
}
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Action/execution failure, invalid arguments, or session not found. |

## Architecture

- `browser_driver.py`: Playwright wrapper instrumented for screenshots, cursor tracking, and full browser perception.
- `agent.py`: action executor and session recorder.
- `mcp_server.py`: MCP server exposing the tools.
- `server.py`: FastAPI WebSocket streaming server (optional dashboard).
- `dashboard/`: vanilla HTML/JS live viewer (optional).
- `bin/`: venv wrapper scripts.
- `scripts/setup.sh`: one-shot install, test, and MCP registration. Set `APPPILOT_LINK_WRAPPERS=1` to also symlink `bin/` wrappers onto PATH.
