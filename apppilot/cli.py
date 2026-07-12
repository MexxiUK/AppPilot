from __future__ import annotations

import asyncio
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any

import click

from apppilot.agent import Agent
from apppilot.browser_driver import BrowserDriver
from apppilot.config import settings
from apppilot.models import Action, Perception, Session, Step
from apppilot.server import (
    broadcast_session_state,
    broadcast_step,
    run_server,
)
from apppilot.utils import find_free_port, wait_for_port


def _send_ws_command(url: str, command: dict, timeout: float = 120.0) -> dict:
    import websockets

    async def _communicate() -> dict:
        async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
            await ws.send(json.dumps(command))
            response = await asyncio.wait_for(ws.recv(), timeout=timeout)
            return json.loads(response)

    return asyncio.run(_communicate())


def _dump(obj: dict) -> None:
    click.echo(json.dumps(obj, indent=2, default=str))


def _perception_to_dict(perception: Perception | None) -> dict[str, Any] | None:
    if perception is None:
        return None
    return {
        "url": perception.url,
        "title": perception.title,
        "viewport": perception.viewport,
        "scroll_position": perception.scroll_position,
        "screenshot_b64": perception.screenshot_b64,
        "accessibility_tree": perception.accessibility_tree,
        "elements": [
            {
                "index": el.index,
                "role": el.role,
                "text": el.text,
                "selector": el.selector,
                "box": el.box.__dict__,
            }
            for el in perception.elements
        ],
        "console_logs": perception.console_logs,
        "network_summary": perception.network_summary,
        "page_errors": perception.page_errors,
        "overlay": perception.overlay,
    }


def _session_summary(session: Session) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "task": session.task,
        "start_url": session.start_url,
        "status": session.status,
        "result": session.result,
        "extracted_data": session.extracted_data,
        "step_count": len(session.steps),
        "output_dir": settings.output_dir,
        "session_path": str(Path(settings.output_dir) / "sessions" / f"{session.session_id}.jsonl"),
        "report_path": str(Path(settings.output_dir) / "reports" / f"{session.session_id}.json"),
        "screenshots_dir": str(Path(settings.output_dir) / "screenshots"),
        "perception": _perception_to_dict(session.final_perception),
        "final_screenshot_b64": session.final_screenshot_b64 or None,
        "steps": [
            {
                "number": s.number,
                "action": {
                    "type": s.action.type,
                    "element_index": s.action.element_index,
                    "x": s.action.x,
                    "y": s.action.y,
                    "selector": s.action.selector,
                    "text": s.action.text,
                    "url": s.action.url,
                    "direction": s.action.direction,
                    "amount": s.action.amount,
                    "duration_ms": s.action.duration_ms,
                    "reason": s.action.reason,
                },
                "error": s.error,
                "extract_result": s.extract_result,
                "target_label": s.target_label,
                "timestamp": s.timestamp.isoformat(),
            }
            for s in session.steps
        ],
    }


@click.group()
def main() -> None:
    """AppPilot — AI-driven visible browser agent."""
    pass


@main.command()
@click.option("--actions", "-a", required=True, help="JSON array of actions to execute")
@click.option("--start-url", default=None, help="Initial URL to open before executing actions")
@click.option("--task", default="executed actions", help="Description of what is being tested")
@click.option("--headless/--headed", default=True, help="Run browser headless or headed")
@click.option("--dashboard/--no-dashboard", default=False, help="Start the streaming dashboard server")
@click.option("--open-browser/--no-open-browser", default=False, help="Open a web browser to the dashboard")
@click.option("--output-dir", default=None, help="Root directory for results, reports, and screenshots")
@click.option("--slow-mo", default=None, type=int, help="Playwright slow-mo in ms (default 500)")
@click.option("--action-delay", default=None, type=int, help="Pause before each action in ms (default 200)")
@click.option("--wait-after", default=None, type=int, help="Pause after each action in ms (default 600)")
@click.option("--json/--human", "json_output", default=True, help="Emit machine-readable JSON output")
def run(
    actions: str,
    start_url: str | None,
    task: str,
    headless: bool,
    dashboard: bool,
    open_browser: bool,
    output_dir: str | None,
    slow_mo: int | None,
    action_delay: int | None,
    wait_after: int | None,
    json_output: bool,
) -> None:
    """Execute a caller-provided list of browser actions and return the resulting state."""
    settings.headless = headless
    if slow_mo is not None:
        settings.slow_mo = slow_mo
    if action_delay is not None:
        settings.action_delay_ms = action_delay
    if wait_after is not None:
        settings.wait_after_action_ms = wait_after
    if output_dir is not None:
        settings.output_dir = output_dir

    try:
        action_list = [Action(**a) for a in json.loads(actions)]
    except json.JSONDecodeError as exc:
        click.echo(f"Invalid actions JSON: {exc}", err=True)
        sys.exit(1)

    server_task: asyncio.Task[Any] | None = None

    async def on_step(step: Step) -> None:
        await broadcast_step(step)

    async def stdin_reader(agent: Agent) -> None:
        """Read terminal input and submit it when the agent asks for human_input."""
        while True:
            await agent.wait_for_human_input()
            prompt = agent.human_input_prompt or "Human input required"
            try:
                text = await asyncio.to_thread(input, f"\n[AppPilot] {prompt}\n> ")
            except EOFError:
                text = ""
            agent.submit_human_input(text)

    async def main_task() -> None:
        nonlocal server_task
        if dashboard:
            settings.port = find_free_port()
            server_task = asyncio.create_task(run_server())
            dashboard_url = f"http://{settings.host}:{settings.port}"
            ready = await wait_for_port(settings.host, settings.port)
            if json_output:
                _dump({"type": "dashboard_ready", "url": dashboard_url})
            if open_browser and ready:
                try:
                    webbrowser.open(dashboard_url)
                except Exception:
                    pass

        agent = Agent(task=task, start_url=start_url, on_step=on_step)
        stdin_task = asyncio.create_task(stdin_reader(agent))

        try:
            session = await agent.run_action_list(action_list)
        finally:
            stdin_task.cancel()
            try:
                await stdin_task
            except asyncio.CancelledError:
                pass

        if dashboard:
            await broadcast_session_state(session)

        summary = _session_summary(session)
        if json_output:
            _dump({"type": "execute_complete", **summary})
        else:
            click.echo(f"Session {session.session_id}: {session.status}")
            if session.result:
                click.echo(f"Result: {session.result}")

        if server_task:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

        if session.status != "success":
            sys.exit(1)

    asyncio.run(main_task())


@main.command()
@click.option("--start-url", default=None, help="Initial URL to open")
@click.option("--headless/--headed", default=True, help="Run browser headless or headed")
@click.option("--dashboard-port", default=None, type=int, help="Port for the dashboard WebSocket. Defaults to a random high port.")
@click.option("--control-port", default=None, type=int, help="Port for the control WebSocket. Defaults to a random high port.")
@click.option("--open-browser/--no-open-browser", default=False, help="Open a web browser to the dashboard")
@click.option("--output-dir", default=None, help="Root directory for results, reports, and screenshots")
@click.option("--slow-mo", default=None, type=int, help="Playwright slow-mo in ms (default 500)")
@click.option("--action-delay", default=None, type=int, help="Pause before each action in ms (default 200)")
@click.option("--wait-after", default=None, type=int, help="Pause after each action in ms (default 600)")
def session(
    start_url: str | None,
    headless: bool,
    dashboard_port: int | None,
    control_port: int | None,
    open_browser: bool,
    output_dir: str | None,
    slow_mo: int | None,
    action_delay: int | None,
    wait_after: int | None,
) -> None:
    """Start a persistent browser session with dashboard and control sockets."""
    settings.headless = headless
    if slow_mo is not None:
        settings.slow_mo = slow_mo
    if action_delay is not None:
        settings.action_delay_ms = action_delay
    if wait_after is not None:
        settings.wait_after_action_ms = wait_after
    if output_dir is not None:
        settings.output_dir = output_dir

    chosen_dashboard_port = dashboard_port or find_free_port()
    chosen_control_port = control_port or find_free_port()
    dashboard_url = f"http://{settings.host}:{chosen_dashboard_port}"
    control_url = f"ws://{settings.host}:{chosen_control_port}/control"

    from apppilot.session_server import run_session_server

    async def _run_session() -> None:
        await run_session_server(
            headless=headless,
            start_url=start_url,
            control_port=chosen_control_port,
            dashboard_port=chosen_dashboard_port,
        )

    async def _main() -> None:
        server_task = asyncio.create_task(_run_session())
        if open_browser:
            ready = await wait_for_port(settings.host, chosen_dashboard_port)
            if ready:
                webbrowser.open(dashboard_url)
        _dump({"type": "session_ready", "dashboard_url": dashboard_url, "control_url": control_url})
        await server_task

    asyncio.run(_main())


@main.command()
@click.option("--actions", "-a", required=True, help="JSON array of actions to execute")
@click.option("--session", "-s", required=True, help="Control WebSocket URL, e.g. ws://localhost:49217/control")
@click.option("--start-url", default=None, help="Initial URL to open before executing actions")
@click.option("--task", default="executed actions", help="Description of what is being tested")
@click.option("--json/--human", "json_output", default=True, help="Emit machine-readable JSON output")
def run_session(
    actions: str,
    session: str,
    start_url: str | None,
    task: str,
    json_output: bool,
) -> None:
    """Send an action batch to a running apppilot session."""
    try:
        action_list = [Action(**a) for a in json.loads(actions)]
    except json.JSONDecodeError as exc:
        click.echo(f"Invalid actions JSON: {exc}", err=True)
        sys.exit(1)

    command = {
        "command": "run",
        "task": task,
        "start_url": start_url,
        "actions": [a.__dict__ for a in action_list],
    }
    result = _send_ws_command(session, command)
    if json_output:
        _dump(result)
    if result.get("type") == "error" or result.get("status") != "success":
        sys.exit(1)


@main.command()
@click.option("--session", "-s", required=True, help="Control WebSocket URL, e.g. ws://localhost:49217/control")
@click.option("--json/--human", "json_output", default=True, help="Emit machine-readable JSON output")
def new_test(
    session: str,
    json_output: bool,
) -> None:
    """Reset the running session for a fresh test (clear dashboard, navigate to about:blank)."""
    result = _send_ws_command(session, {"command": "new_test"})
    if json_output:
        _dump(result)
    if result.get("type") == "error":
        sys.exit(1)


@main.command()
@click.argument("url")
@click.option("--headless/--headed", default=True, help="Run browser headless or headed")
@click.option("--dashboard/--no-dashboard", default=False, help="Start the streaming dashboard server")
@click.option("--json/--human", default=True, help="Emit machine-readable JSON output")
def snapshot(url: str, headless: bool, dashboard: bool, json_output: bool) -> None:
    """Load a URL and emit a screenshot/state payload."""
    settings.headless = headless
    server_task: asyncio.Task[Any] | None = None

    async def main_task() -> None:
        nonlocal server_task
        if dashboard:
            settings.port = find_free_port()
            server_task = asyncio.create_task(run_server())
            await wait_for_port(settings.host, settings.port)

        driver = BrowserDriver()
        await driver.start(url)
        shot = await driver.screenshot()
        page_title = await driver.page.title()
        page_url = driver.page.url
        await driver.stop()

        payload = {
            "type": "snapshot",
            "url": page_url,
            "title": page_title,
            "screenshot_b64": Step.b64_from_bytes(shot),
        }
        if json_output:
            _dump(payload)
        else:
            click.echo(f"Snapshot: {page_url} — {page_title}")

        if server_task:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    asyncio.run(main_task())


@main.command()
@click.option("--port", default=None, type=int, help="Port for the dashboard server. Defaults to a random high port.")
@click.option("--json/--human", default=False, help="Emit machine-readable JSON output")
def serve(port: int | None, json_output: bool) -> None:
    """Start the dashboard server only."""
    chosen_port = port or find_free_port()
    settings.port = chosen_port
    url = f"http://{settings.host}:{chosen_port}"
    if json_output:
        _dump({"type": "server_ready", "url": url})
    else:
        click.echo(f"Dashboard server: {url}")
    asyncio.run(run_server())


@main.command()
@click.argument("session_id")
@click.option("--port", default=None, type=int)
@click.option("--open-browser/--no-open-browser", default=False, help="Open a web browser to the dashboard")
@click.option("--json/--human", default=True, help="Emit machine-readable JSON output")
def replay(
    session_id: str,
    port: int | None,
    open_browser: bool,
    json_output: bool,
) -> None:
    """Replay a saved session over the dashboard WebSocket."""
    chosen_port = port or find_free_port()
    settings.port = chosen_port
    session_path = Path(settings.output_dir) / "sessions" / f"{session_id}.jsonl"
    if not session_path.exists():
        if json_output:
            _dump({"type": "error", "message": f"Session not found: {session_path}"})
        else:
            click.echo(f"Session not found: {session_path}", err=True)
        sys.exit(1)

    url = f"http://{settings.host}:{chosen_port}"
    if json_output:
        _dump({"type": "replay_ready", "session_id": session_id, "url": url})

    async def replay_task() -> None:
        server_task = asyncio.create_task(run_server())
        ready = await wait_for_port(settings.host, chosen_port)

        if open_browser and ready:
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass

        with session_path.open("r", encoding="utf-8") as f:
            for line in f:
                record = json.loads(line)
                await broadcast_step(_record_to_step_payload(record))
                await asyncio.sleep(1.0)

        if json_output:
            _dump({"type": "replay_complete", "session_id": session_id})
        else:
            click.echo("Replay complete.")

        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    asyncio.run(replay_task())


@main.command()
@click.argument("session_id")
@click.option("--json/--human", default=True, help="Emit machine-readable JSON output")
def inspect(session_id: str, json_output: bool) -> None:
    """Print a saved session summary without replaying it."""
    session_path = Path(settings.output_dir) / "sessions" / f"{session_id}.jsonl"
    if not session_path.exists():
        if json_output:
            _dump({"type": "error", "message": f"Session not found: {session_path}"})
        else:
            click.echo(f"Session not found: {session_path}", err=True)
        sys.exit(1)

    with session_path.open("r", encoding="utf-8") as f:
        first = json.loads(f.readline())
    summary = {
        "type": "session",
        "session_id": first["session_id"],
        "task": first["task"],
        "start_url": first["start_url"],
        "status": first["status"],
        "result": first["result"],
        "session_path": str(session_path),
    }
    if json_output:
        _dump(summary)
    else:
        for k, v in summary.items():
            click.echo(f"{k}: {v}")


def _record_to_step_payload(record: dict) -> dict:
    return {
        "type": "step",
        "number": record["number"],
        "action": record["action"],
        "before_screenshot_b64": record.get("before_screenshot_b64"),
        "after_screenshot_b64": record.get("after_screenshot_b64"),
        "cursor_start": record.get("cursor_start"),
        "cursor_end": record.get("cursor_end"),
        "target_box": record.get("target_box"),
        "target_label": record.get("target_label"),
        "perception": record.get("perception"),
        "error": record.get("error"),
        "timestamp": record.get("timestamp"),
    }


if __name__ == "__main__":
    main()
