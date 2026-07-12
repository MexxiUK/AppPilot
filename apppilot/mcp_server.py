from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import websockets
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    ImageContent,
    TextContent,
    Tool,
)

from apppilot.agent import Agent
from apppilot.browser_driver import BrowserDriver
from apppilot.config import settings
from apppilot.models import Action, Step
from apppilot.server import broadcast_session_state, broadcast_step, run_server
from apppilot.utils import find_free_port, wait_for_port


def _action_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": [
                    "goto", "click", "hover", "type", "scroll", "wait",
                    "screenshot", "human_input", "keypress", "keydown", "keyup",
                    "mousedown", "mouseup", "drag", "dismiss_popup", "finish", "fail",
                    "extract",
                ],
                "description": "Action type to execute.",
            },
            "element_index": {"type": "integer", "description": "Index from the perception element list. Preferred targeting method."},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "end_x": {"type": "number"},
            "end_y": {"type": "number"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "key": {"type": "string", "description": "Key for keypress/keydown/keyup, e.g. ArrowUp, Enter, w, Space."},
            "url": {"type": "string"},
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer"},
            "duration_ms": {"type": "integer"},
            "reason": {"type": "string"},
            "prompt": {"type": "string", "description": "For human_input: message shown to the human operator."},
            "extract_schema": {
                "type": "object",
                "description": (
                    "For extract: JSON schema mapping output keys to selectors. "
                    "Use {\"selector\": \"...\", \"attribute\": \"text|html|...\", "
                    "\"multiple\": true, \"fields\": {...}}."
                ),
            },
        },
        "required": ["type"],
    }


def _apply_settings(args: dict[str, Any]) -> None:
    if "headless" in args and args["headless"] is not None:
        settings.headless = args["headless"]
    if "slow_mo" in args and args["slow_mo"] is not None:
        settings.slow_mo = args["slow_mo"]
    if "action_delay" in args and args["action_delay"] is not None:
        settings.action_delay_ms = args["action_delay"]
    if "wait_after" in args and args["wait_after"] is not None:
        settings.wait_after_action_ms = args["wait_after"]


TOOLS: list[Tool] = [
    Tool(
        name="apppilot_execute",
        description=(
            "Execute a sequence of browser actions and return the resulting page state. "
            "The caller (an AI) decides the actions; this tool only drives the browser. "
            "Returns the final perception (URL, title, interactive elements, logs) and a screenshot, "
            "unless the last action is extract, in which case no screenshot is returned."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": _action_schema(),
                    "description": "List of actions to execute in order.",
                },
                "start_url": {
                    "type": "string",
                    "description": "Optional initial URL to open before executing actions.",
                },
                "task": {
                    "type": "string",
                    "description": "Optional description of what these actions are testing. Used for the saved session record.",
                },
                "headless": {
                    "type": "boolean",
                    "default": True,
                    "description": "Run the browser without a visible window.",
                },
                "dashboard": {
                    "type": "boolean",
                    "default": False,
                    "description": "Start the live streaming dashboard server.",
                },
            },
            "required": ["actions"],
        },
    ),
    Tool(
        name="apppilot_snapshot",
        description=(
            "Load a URL in the browser and return a screenshot plus page metadata."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "headless": {"type": "boolean", "default": True},
                "dashboard": {"type": "boolean", "default": False},
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="apppilot_inspect",
        description=("Print a summary of a saved session without replaying it."),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="apppilot_replay",
        description=("Replay a saved browser session over the dashboard WebSocket."),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "port": {"type": "integer"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="apppilot_run_session",
        description=(
            "Send a batch of actions to a running persistent AppPilot session via its control WebSocket. "
            "The dashboard associated with that session will update live. "
            "Use this after starting a session with `apppilot session`."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": "Control WebSocket URL, e.g. ws://127.0.0.1:42218/control",
                },
                "actions": {
                    "type": "array",
                    "items": _action_schema(),
                    "description": "List of actions to execute on the existing session.",
                },
                "start_url": {
                    "type": "string",
                    "description": "Optional initial URL to open before executing actions.",
                },
                "task": {
                    "type": "string",
                    "description": "Optional description for the saved session record.",
                },
            },
            "required": ["session", "actions"],
        },
    ),
]


app = Server("apppilot")


def _perception_to_dict(perception) -> dict | None:
    if perception is None:
        return None
    return {
        "url": perception.url,
        "title": perception.title,
        "viewport": perception.viewport,
        "scroll_position": perception.scroll_position,
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
    }


def _step_to_dict(step: Step) -> dict:
    return {
        "number": step.number,
        "action": {
            "type": step.action.type,
            "element_index": step.action.element_index,
            "x": step.action.x,
            "y": step.action.y,
            "end_x": step.action.end_x,
            "end_y": step.action.end_y,
            "selector": step.action.selector,
            "text": step.action.text,
            "key": step.action.key,
            "url": step.action.url,
            "direction": step.action.direction,
            "amount": step.action.amount,
            "duration_ms": step.action.duration_ms,
            "reason": step.action.reason,
        },
        "error": step.error,
        "extract_result": step.extract_result,
        "target_label": step.target_label,
    }


def _text_content(obj: dict) -> TextContent:
    return TextContent(type="text", text=json.dumps(obj, indent=2, default=str))


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[Any]:
    args = dict(arguments or {})

    if name == "apppilot_execute":
        _apply_settings(args)
        task = args.get("task", "executed actions")
        start_url = args.get("start_url")
        actions = [Action(**a) for a in args["actions"]]
        execute_server_task: asyncio.Task[Any] | None = None

        async def on_step(step: Step) -> None:
            await broadcast_step(step)

        async def do_execute() -> list[Any]:
            nonlocal execute_server_task
            if args.get("dashboard"):
                settings.port = find_free_port()
                execute_server_task = asyncio.create_task(run_server())
                await wait_for_port(settings.host, settings.port)

            agent = Agent(task=task, start_url=start_url, on_step=on_step)
            session = await agent.run_action_list(actions)

            if execute_server_task:
                await broadcast_session_state(session)
                execute_server_task.cancel()
                try:
                    await execute_server_task
                except asyncio.CancelledError:
                    pass

            last_perception = session.final_perception or (session.steps[-1].perception if session.steps else None)
            last_screenshot_b64 = session.final_screenshot_b64 or ""
            if not last_screenshot_b64 and session.steps:
                last_screenshot_b64 = session.steps[-1].after_screenshot_b64 or session.steps[-1].before_screenshot_b64 or ""

            result = {
                "type": "execute_complete",
                "session_id": session.session_id,
                "task": session.task,
                "status": session.status,
                "result": session.result,
                "extracted_data": session.extracted_data,
                "step_count": len(session.steps),
                "output_dir": settings.output_dir,
                "session_path": str(Path(settings.output_dir) / "sessions" / f"{session.session_id}.jsonl"),
                "report_path": str(Path(settings.output_dir) / "reports" / f"{session.session_id}.json"),
                "screenshots_dir": str(Path(settings.output_dir) / "screenshots"),
                "steps": [_step_to_dict(s) for s in session.steps],
                "perception": _perception_to_dict(last_perception),
            }
            contents: list[Any] = [_text_content(result)]
            last_action_type = session.steps[-1].action.type if session.steps else None
            if last_screenshot_b64 and last_action_type != "extract":
                contents.append(ImageContent(type="image", data=last_screenshot_b64, mimeType="image/jpeg"))
            return contents

        return await do_execute()

    if name == "apppilot_snapshot":
        settings.headless = args.get("headless", True)
        snapshot_server_task: asyncio.Task[Any] | None = None

        async def do_snapshot() -> list[Any]:
            nonlocal snapshot_server_task
            if args.get("dashboard"):
                settings.port = find_free_port()
                snapshot_server_task = asyncio.create_task(run_server())
                await wait_for_port(settings.host, settings.port)

            driver = BrowserDriver()
            await driver.start(args["url"])
            shot = await driver.screenshot()
            perception = await driver.perceive()
            title = perception.title
            url = perception.url
            await driver.stop()

            if snapshot_server_task:
                snapshot_server_task.cancel()
                try:
                    await snapshot_server_task
                except asyncio.CancelledError:
                    pass

            return [
                _text_content({"type": "snapshot", "url": url, "title": title, "perception": _perception_to_dict(perception)}),
                ImageContent(
                    type="image",
                    data=base64.b64encode(shot).decode("ascii"),
                    mimeType="image/jpeg",
                ),
            ]

        return await do_snapshot()

    if name == "apppilot_inspect":
        session_id = args["session_id"]
        session_path = Path(settings.output_dir) / "sessions" / f"{session_id}.jsonl"
        if not session_path.exists():
            return [_text_content({"type": "error", "message": f"Session not found: {session_path}"})]
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
        return [_text_content(summary)]

    if name == "apppilot_replay":
        session_id = args["session_id"]
        if "port" in args and args["port"] is not None:
            settings.port = args["port"]
        session_path = Path(settings.output_dir) / "sessions" / f"{session_id}.jsonl"
        if not session_path.exists():
            return [_text_content({"type": "error", "message": f"Session not found: {session_path}"})]

        async def do_replay() -> list[Any]:
            replay_port = find_free_port()
            settings.port = replay_port
            server_task = asyncio.create_task(run_server())
            await wait_for_port(settings.host, replay_port)
            url = f"http://{settings.host}:{replay_port}"

            with session_path.open("r", encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line)
                    await broadcast_step(_record_to_step_payload(record))
                    await asyncio.sleep(1.0)

            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

            return [_text_content({"type": "replay_complete", "session_id": session_id, "url": url})]

        return await do_replay()

    if name == "apppilot_run_session":
        session_url = args["session"]
        task = args.get("task", "executed actions")
        start_url = args.get("start_url")
        actions = args["actions"]

        async def do_run_session() -> list[Any]:
            try:
                async with websockets.connect(session_url) as ws:
                    await ws.send(json.dumps({
                        "command": "run",
                        "actions": actions,
                        "task": task,
                        "start_url": start_url,
                    }))
                    # Wait for the result message from the control socket.
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") in ("execute_complete", "error"):
                            return [_text_content(msg)]
                    return [_text_content({"type": "error", "message": "Session closed without returning a result."})]
            except Exception as exc:  # noqa: BLE001
                return [_text_content({"type": "error", "message": f"Failed to reach session at {session_url}: {exc}"})]

        return await do_run_session()

    return [_text_content({"type": "error", "message": f"Unknown tool: {name}"})]


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


def _ensure_output_dir() -> None:
    """The MCP server requires BC_OUTPUT_DIR to be set by its environment."""
    if not settings.output_dir:
        raise RuntimeError(
            "BC_OUTPUT_DIR environment variable is required for the apppilot MCP server."
        )


async def run_mcp_server() -> None:
    _ensure_output_dir()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


def main() -> None:
    _ensure_output_dir()
    asyncio.run(run_mcp_server())


if __name__ == "__main__":
    main()
