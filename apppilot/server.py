from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from apppilot.config import settings
from apppilot.models import Session, Step

app = FastAPI(title="AppPilot")

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"


class ConnectionManager:
    def __init__(self, history_limit: int = 100) -> None:
        self.connections: list[WebSocket] = []
        self.history: list[dict] = []
        self.history_limit = history_limit
        self._last_session: dict | None = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        if self._last_session:
            await websocket.send_json(self._last_session)
        for msg in self.history:
            await websocket.send_json(msg)
        self.connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.connections:
            self.connections.remove(websocket)

    def clear_history(self) -> None:
        self.history.clear()
        self._last_session = None

    async def broadcast(self, message: dict) -> None:
        if message.get("type") == "step":
            self.history.append(message)
            if len(self.history) > self.history_limit:
                self.history.pop(0)
        elif message.get("type") == "session":
            self._last_session = message
        elif message.get("type") == "reset":
            self.history.clear()
            self._last_session = None

        dead: list[WebSocket] = []
        for ws in self.connections:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """One-way broadcast channel: server pushes steps/session state to the dashboard."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive; the dashboard only receives broadcasts.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


def make_step_payload(step: Step) -> dict:
    return {
        "type": "step",
        "number": step.number,
        "action": {
            "type": step.action.type,
            "element_index": step.action.element_index,
            "x": step.action.x,
            "y": step.action.y,
            "selector": step.action.selector,
            "text": step.action.text,
            "url": step.action.url,
            "direction": step.action.direction,
            "amount": step.action.amount,
            "duration_ms": step.action.duration_ms,
            "reason": step.action.reason,
        },
        "before_screenshot_b64": step.before_screenshot_b64,
        "after_screenshot_b64": step.after_screenshot_b64,
        "cursor_start": step.cursor_start.__dict__ if step.cursor_start else None,
        "cursor_end": step.cursor_end.__dict__ if step.cursor_end else None,
        "target_box": step.target_box.__dict__ if step.target_box else None,
        "target_label": step.target_label,
        "perception": _perception_to_dict(step.perception),
        "error": step.error,
        "extract_result": step.extract_result,
        "timestamp": step.timestamp.isoformat(),
    }


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
        "overlay": perception.overlay,
    }


async def broadcast_step(step: Step) -> None:
    await manager.broadcast(make_step_payload(step))


async def broadcast_session_state(session: Session) -> None:
    await manager.broadcast(
        {
            "type": "session",
            "session_id": session.session_id,
            "task": session.task,
            "start_url": session.start_url,
            "status": session.status,
            "result": session.result,
            "step_count": len(session.steps),
        }
    )


# Mount dashboard static files last so API routes take precedence.
if DASHBOARD_DIR.exists():
    app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")


async def run_server() -> None:
    import uvicorn

    from apppilot.utils import find_free_port

    port = settings.port or find_free_port()
    config = uvicorn.Config(app, host=settings.host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
