from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

ActionType = Literal[
    "goto",
    "click",
    "hover",
    "type",
    "scroll",
    "wait",
    "screenshot",
    "human_input",
    "dismiss_popup",
    "finish",
    "fail",
    "keypress",
    "keydown",
    "keyup",
    "mousedown",
    "mouseup",
    "drag",
    "extract",
    "session_start",
]



@dataclass
class Point:
    x: float
    y: float


@dataclass
class Action:
    type: ActionType
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    text: str | None = None
    url: str | None = None
    selector: str | None = None
    element_index: int | None = None
    key: str | None = None
    direction: Literal["up", "down"] | None = None
    amount: int | None = None
    duration_ms: int | None = None
    reason: str = ""
    prompt: str | None = None  # used by human_input
    extract_schema: dict[str, Any] | None = None  # used by extract


@dataclass
class BoundingBox:
    x: float
    y: float
    width: float
    height: float


@dataclass
class ElementHint:
    index: int
    role: str
    text: str
    selector: str
    box: BoundingBox


@dataclass
class Perception:
    url: str
    title: str
    viewport: dict[str, int]
    scroll_position: dict[str, float]
    screenshot_b64: str = ""
    accessibility_tree: dict | None = None
    elements: list[ElementHint] = field(default_factory=list)
    console_logs: list[dict] = field(default_factory=list)
    network_summary: dict = field(default_factory=dict)
    page_errors: list[str] = field(default_factory=list)
    overlay: dict | None = None


@dataclass
class Step:
    number: int
    action: Action
    before_screenshot_b64: str | None = None
    after_screenshot_b64: str | None = None
    cursor_start: Point | None = None
    cursor_end: Point | None = None
    target_box: BoundingBox | None = None
    target_label: str | None = None
    perception: Perception | None = None
    error: str | None = None
    extract_result: dict[str, Any] | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @staticmethod
    def b64_from_bytes(data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")


@dataclass
class Session:
    session_id: str
    task: str
    start_url: str | None = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    steps: list[Step] = field(default_factory=list)
    status: Literal["running", "success", "failed"] = "running"
    result: str | None = None
    extracted_data: dict[str, Any] | None = None
    final_perception: Perception | None = None
    final_screenshot_b64: str = ""
