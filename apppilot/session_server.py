from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from apppilot.agent import Agent
from apppilot.browser_driver import BrowserDriver
from apppilot.config import settings
from apppilot.models import Action, Step
from apppilot.server import (
    _perception_to_dict,
    broadcast_session_state,
    broadcast_step,
    make_step_payload,
    manager as dashboard_manager,
)

app = FastAPI(title="AppPilot Session")

CONTROL_CONNECTIONS: list[WebSocket] = []
CURRENT_AGENT: Agent | None = None
CURRENT_DRIVER: BrowserDriver | None = None


def _make_session_message(agent: Agent) -> dict:
    return {
        "type": "session",
        "session_id": agent.session.session_id,
        "task": agent.session.task,
        "start_url": agent.session.start_url,
        "status": agent.session.status,
        "result": agent.session.result,
        "step_count": len(agent.session.steps),
    }


async def _send_result(ws: WebSocket, result: dict) -> None:
    try:
        await ws.send_json(result)
    except Exception:  # noqa: BLE001
        pass


async def _on_agent_step(step) -> None:
    await broadcast_step(step)


async def _handle_command(msg: dict, ws: WebSocket) -> None:
    global CURRENT_AGENT, CURRENT_DRIVER

    cmd = msg.get("command")
    if cmd == "run":
        if CURRENT_AGENT is None:
            await _send_result(ws, {"type": "error", "message": "No active session. Run `apppilot session` first."})
            return

        task = msg.get("task", "executed actions")
        start_url = msg.get("start_url")
        actions = [Action(**a) for a in msg.get("actions", [])]

        agent = Agent(task=task, start_url=start_url, on_step=_on_agent_step, driver=CURRENT_DRIVER)
        try:
            session = await agent.run_action_list(actions)
        except Exception as exc:  # noqa: BLE001
            await _send_result(ws, {"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return

        await broadcast_session_state(session)
        await _send_result(ws, {
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
            "steps": [make_step_payload(s) for s in session.steps],
        })
        return

    if cmd == "new_test":
        if CURRENT_DRIVER is None:
            await _send_result(ws, {"type": "error", "message": "No active session."})
            return

        try:
            await CURRENT_DRIVER.page.goto("about:blank", wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            await _send_result(ws, {"type": "error", "message": f"Failed to reset page: {exc}"})
            return

        await dashboard_manager.broadcast({"type": "reset"})
        await _send_result(ws, {"type": "new_test_complete"})
        return

    if cmd == "snapshot":
        if CURRENT_DRIVER is None:
            await _send_result(ws, {"type": "error", "message": "No active session."})
            return
        try:
            perception = await CURRENT_DRIVER.perceive()
            shot = await CURRENT_DRIVER.screenshot()
            perception.screenshot_b64 = Step.b64_from_bytes(shot)
        except Exception as exc:  # noqa: BLE001
            await _send_result(ws, {"type": "error", "message": f"Snapshot failed: {exc}"})
            return

        await _send_result(ws, {"type": "snapshot", "perception": _perception_to_dict(perception)})
        return

    await _send_result(ws, {"type": "error", "message": f"Unknown command: {cmd}"})


@app.websocket("/control")
async def control_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    CONTROL_CONNECTIONS.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            await _handle_command(msg, websocket)
    except WebSocketDisconnect:
        CONTROL_CONNECTIONS.remove(websocket)
    except Exception:  # noqa: BLE001
        if websocket in CONTROL_CONNECTIONS:
            CONTROL_CONNECTIONS.remove(websocket)


async def run_session_server(
    *,
    headless: bool = True,
    start_url: str | None = None,
    control_port: int | None = None,
    dashboard_port: int | None = None,
) -> None:
    global CURRENT_AGENT, CURRENT_DRIVER

    from apppilot.utils import find_free_port

    chosen_control_port = control_port or find_free_port()
    chosen_dashboard_port = dashboard_port or find_free_port()

    settings.headless = headless
    CURRENT_DRIVER = BrowserDriver()
    await CURRENT_DRIVER.start(start_url)
    CURRENT_AGENT = Agent(task="persistent session", start_url=start_url, on_step=_on_agent_step, driver=CURRENT_DRIVER)

    # Capture and broadcast the initial page state so the dashboard isn't blank
    # until the first action arrives.
    initial_perception = await CURRENT_DRIVER.perceive()
    initial_shot = await CURRENT_DRIVER.screenshot()
    initial_step = Step(
        number=0,
        action=Action(type="session_start", reason="Session started"),
        before_screenshot_b64="",
        after_screenshot_b64=Step.b64_from_bytes(initial_shot),
        perception=initial_perception,
    )
    await broadcast_session_state(CURRENT_AGENT.session)
    await broadcast_step(initial_step)

    dashboard_task = asyncio.create_task(run_dashboard_server_on_port(chosen_dashboard_port))
    control_task = asyncio.create_task(run_control_server_on_port(chosen_control_port))

    await asyncio.gather(dashboard_task, control_task)


async def run_dashboard_server_on_port(port: int) -> None:
    import uvicorn
    # Re-mount static files on a fresh app instance bound to the given port.
    dash_app = FastAPI()

    @dash_app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await dashboard_manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            dashboard_manager.disconnect(websocket)

    # Mount static files last so API/WebSocket routes take precedence.
    dash_app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "dashboard", html=True), name="dashboard")

    config = uvicorn.Config(
        dash_app, host=settings.host, port=port, log_level="info", ws_max_size=16 * 1024 * 1024
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_control_server_on_port(port: int) -> None:
    import uvicorn
    config = uvicorn.Config(
        app, host=settings.host, port=port, log_level="info", ws_max_size=16 * 1024 * 1024
    )
    server = uvicorn.Server(config)
    await server.serve()


async def close_session() -> None:
    global CURRENT_DRIVER
    if CURRENT_DRIVER:
        await CURRENT_DRIVER.stop()
        CURRENT_DRIVER = None
