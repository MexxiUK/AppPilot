# Contributing to AppPilot

Thanks for helping make AppPilot better.

## Quick start

```bash
git clone git@github.com:MexxiUK/AppPilot.git apppilot
cd apppilot
./scripts/setup.sh
```

## Development workflow

1. Create a branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Make your changes.
3. Run the tests and linters:
   ```bash
   .venv/bin/python -m pytest tests/ -q
   .venv/bin/ruff check apppilot tests
   .venv/bin/mypy apppilot || true
   ```
4. Commit with a clear message.
5. Push and open a pull request.

## Project layout

- `apppilot/browser_driver.py` — Playwright wrapper.
- `apppilot/agent.py` — action executor and session recorder.
- `apppilot/mcp_server.py` — MCP server exposing the tools.
- `apppilot/server.py` — FastAPI WebSocket streaming server.
- `apppilot/session_server.py` — persistent session control WebSocket.
- `apppilot/cli.py` — terminal commands.
- `apppilot/utils.py` — shared helpers.
- `dashboard/` — vanilla HTML/JS live viewer.
- `tests/` — pytest suite.

## Release process

Maintainers tag releases from `main`:

```bash
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0
```

## Code of conduct

Be respectful, stay on topic, and keep feedback constructive.
