from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from apppilot.browser_driver import BrowserDriver
from apppilot.config import require_output_dir
from apppilot.models import Action, Perception, Session, Step

StepCallback = Callable[[Step], Awaitable[None]]


class Agent:
    def __init__(
        self,
        task: str,
        start_url: str | None = None,
        on_step: StepCallback | None = None,
        driver: BrowserDriver | None = None,
    ) -> None:
        self.task = task
        self.start_url = start_url
        self.on_step = on_step
        self.driver = driver if driver is not None else BrowserDriver()
        self._owns_driver = driver is None
        self.session = Session(
            session_id=uuid.uuid4().hex[:12],
            task=task,
            start_url=start_url,
        )
        self._pending_human_input: asyncio.Future[str] | None = None
        self._human_input_event: asyncio.Event = asyncio.Event()
        self._human_input_prompt: str | None = None
        self.output_dir = Path(require_output_dir())
        self._sessions_dir = self.output_dir / "sessions"
        self.screenshots_dir = self.output_dir / "screenshots"
        self.reports_dir = self.output_dir / "reports"
        self._stopped = False

    @property
    def is_waiting_for_human_input(self) -> bool:
        return self._human_input_event.is_set()

    @property
    def human_input_prompt(self) -> str | None:
        return self._human_input_prompt

    async def wait_for_human_input(self) -> None:
        await self._human_input_event.wait()

    async def run_action_list(self, actions: list[Action]) -> Session:
        """Execute a caller-provided list of actions. No LLM is used."""
        if self.driver._page is None:
            await self.driver.start(self.start_url)
        try:
            for step_number, action in enumerate(actions, start=1):
                if self._stopped:
                    break

                perception = await self.driver.perceive()
                perception.screenshot_b64 = Step.b64_from_bytes(await self.driver.screenshot())

                if action.type == "human_input":
                    step = await self._human_input_step(action, perception)
                    step.number = step_number
                    step.before_screenshot_b64 = perception.screenshot_b64
                    step.perception = perception
                    self.session.steps.append(step)
                    if self.on_step:
                        await self.on_step(step)
                    continue

                step = await self.driver.execute(action, perception=perception)
                step.number = step_number
                step.before_screenshot_b64 = perception.screenshot_b64
                step.perception = perception

                self.session.steps.append(step)

                if self.on_step:
                    await self.on_step(step)

                if action.type in ("finish", "fail"):
                    self.session.status = "success" if action.type == "finish" else "failed"
                    self.session.result = action.text
                    break
            else:
                if self.session.status == "running":
                    self.session.status = "success"
                    self.session.result = "All actions completed"

            if self.session.status != "running":
                try:
                    final_perception = await self.driver.perceive()
                    final_perception.screenshot_b64 = Step.b64_from_bytes(await self.driver.screenshot())
                    self.session.final_perception = final_perception
                    self.session.final_screenshot_b64 = final_perception.screenshot_b64
                except Exception:  # noqa: BLE001
                    pass

            # Surface the last successful extraction result on the session.
            for step in reversed(self.session.steps):
                if step.extract_result is not None:
                    self.session.extracted_data = step.extract_result
                    if not self.session.result or self.session.result == "All actions completed":
                        self.session.result = "Extracted data from page"
                    break

            if not self.session.steps:
                self.session.status = "failed"
                self.session.result = "No actions were provided"

        except Exception as exc:
            self.session.status = "failed"
            self.session.result = f"Agent error: {type(exc).__name__}: {exc}"
            raise
        finally:
            if self._owns_driver:
                await self.driver.stop()
            await self._save_session()

        return self.session

    def stop(self) -> None:
        self._stopped = True

    def submit_human_input(self, text: str) -> None:
        if self._pending_human_input and not self._pending_human_input.done():
            self._pending_human_input.set_result(text)
            self._human_input_event.clear()
            self._human_input_prompt = None

    async def _human_input_step(self, action: Action, perception: Perception) -> Step:
        self._pending_human_input = asyncio.get_event_loop().create_future()
        prompt = action.prompt or "Human input required."
        self._human_input_prompt = prompt
        self._human_input_event.set()
        # Broadcast a special message so dashboards/MCP clients can show the prompt and screenshot.
        if self.on_step:
            await self.on_step(
                Step(
                    number=-1,
                    action=Action(type="human_input", prompt=prompt, reason=action.reason),
                    before_screenshot_b64=perception.screenshot_b64,
                    after_screenshot_b64=perception.screenshot_b64,
                    perception=perception,
                )
            )
        try:
            result = await self._pending_human_input
        except asyncio.CancelledError:
            result = ""
        finally:
            self._pending_human_input = None
            self._human_input_event.clear()
            self._human_input_prompt = None
        return Step(
            number=-1,
            action=Action(type="human_input", text=result, prompt=prompt, reason=action.reason),
            before_screenshot_b64=perception.screenshot_b64,
            after_screenshot_b64=perception.screenshot_b64,
            perception=perception,
        )

    def _perception_to_dict(self, perception: Perception | None) -> dict | None:
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
        }

    async def _save_session(self) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        session_path = self._sessions_dir / f"{self.session.session_id}.jsonl"

        # Save screenshots to disk and store relative paths instead of base64.
        records: list[dict] = []
        for step in self.session.steps:
            before_path = self._write_screenshot(step.before_screenshot_b64, step.number, "before")
            after_path = self._write_screenshot(step.after_screenshot_b64, step.number, "after")

            records.append({
                "session_id": self.session.session_id,
                "task": self.session.task,
                "start_url": self.session.start_url,
                "status": self.session.status,
                "result": self.session.result,
                "number": step.number,
                "before_screenshot_path": str(before_path) if before_path else None,
                "after_screenshot_path": str(after_path) if after_path else None,
                "action": {
                    "type": step.action.type,
                    "x": step.action.x,
                    "y": step.action.y,
                    "end_x": step.action.end_x,
                    "end_y": step.action.end_y,
                    "selector": step.action.selector,
                    "text": step.action.text,
                    "url": step.action.url,
                    "element_index": step.action.element_index,
                    "key": step.action.key,
                    "direction": step.action.direction,
                    "amount": step.action.amount,
                    "duration_ms": step.action.duration_ms,
                    "reason": step.action.reason,
                },
                "cursor_start": step.cursor_start.__dict__ if step.cursor_start else None,
                "cursor_end": step.cursor_end.__dict__ if step.cursor_end else None,
                "target_box": step.target_box.__dict__ if step.target_box else None,
                "target_label": step.target_label,
                "perception": self._perception_to_dict(step.perception),
                "error": step.error,
                "extract_result": step.extract_result,
                "timestamp": step.timestamp.isoformat(),
            })

        with session_path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, default=str) + "\n")

        report_path = self.reports_dir / f"{self.session.session_id}.json"
        report = {
            "session_id": self.session.session_id,
            "task": self.session.task,
            "start_url": self.session.start_url,
            "status": self.session.status,
            "result": self.session.result,
            "extracted_data": self.session.extracted_data,
            "step_count": len(self.session.steps),
            "session_path": str(session_path),
            "screenshots_dir": str(self.screenshots_dir),
            "errors": [s.error for s in self.session.steps if s.error],
        }
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

    def _write_screenshot(self, b64: str | None, step_number: int, suffix: str) -> Path | None:
        if not b64:
            return None
        try:
            data = base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return None
        path = self.screenshots_dir / f"{self.session.session_id}_step{step_number}_{suffix}.jpg"
        path.write_bytes(data)
        return path.relative_to(self.output_dir)
