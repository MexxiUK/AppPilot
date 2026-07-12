from __future__ import annotations

from typing import Any, cast

from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright

from apppilot.config import settings
from apppilot.models import Action, BoundingBox, ElementHint, Perception, Point, Step


class BrowserDriver:
    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cursor = Point(
            x=settings.window_width / 2,
            y=settings.window_height / 2,
        )
        self._console_logs: list[dict] = []
        self._network_requests: list[dict] = []
        self._page_errors: list[str] = []

    @property
    def cursor(self) -> Point:
        return self._cursor

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserDriver not started")
        return self._page

    async def start(self, start_url: str | None = None) -> None:
        self._playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {"headless": settings.headless}
        chrome_args = [
            f"--window-size={settings.window_width},{settings.window_height}",
            "--disable-infobars",
        ]
        if settings.headless:
            chrome_args.extend([
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
            ])
        launch_kwargs["args"] = chrome_args
        if settings.browser == "chromium":
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        elif settings.browser == "firefox":
            self._browser = await self._playwright.firefox.launch(**launch_kwargs)
        elif settings.browser == "webkit":
            self._browser = await self._playwright.webkit.launch(**launch_kwargs)
        else:
            raise ValueError(f"Unsupported browser: {settings.browser}")

        self._context = await self._browser.new_context(
            viewport={"width": settings.window_width, "height": settings.window_height},
            device_scale_factor=1,
        )
        self._page = await self._context.new_page()
        # Enforce the exact page viewport so screenshots match the window.
        await self._page.set_viewport_size(
            {"width": settings.window_width, "height": settings.window_height}
        )

        self._attach_listeners(self._page)

        if start_url:
            await self._page.goto(start_url, wait_until="domcontentloaded")
        else:
            await self._page.goto("about:blank")

    def _attach_listeners(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    def _on_console(self, msg) -> None:
        try:
            self._console_logs.append(
                {
                    "type": msg.type,
                    "text": msg.text,
                    "location": getattr(msg, "location", None),
                }
            )
            if len(self._console_logs) > 200:
                self._console_logs.pop(0)
        except Exception:  # noqa: BLE001
            pass

    def _on_pageerror(self, error) -> None:
        try:
            self._page_errors.append(str(error))
            if len(self._page_errors) > 50:
                self._page_errors.pop(0)
        except Exception:  # noqa: BLE001
            pass

    def _on_request(self, request) -> None:
        try:
            self._network_requests.append(
                {
                    "event": "request",
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                }
            )
            if len(self._network_requests) > 200:
                self._network_requests.pop(0)
        except Exception:  # noqa: BLE001
            pass

    def _on_response(self, response) -> None:
        try:
            self._network_requests.append(
                {
                    "event": "response",
                    "url": response.url,
                    "status": response.status,
                    "resource_type": response.request.resource_type if response.request else "unknown",
                }
            )
            if len(self._network_requests) > 200:
                self._network_requests.pop(0)
        except Exception:  # noqa: BLE001
            pass

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(type="jpeg", quality=85, full_page=False)

    async def perceive(self) -> Perception:
        page = self.page
        try:
            accessibility = await page.accessibility.snapshot()
        except Exception:  # noqa: BLE001
            accessibility = None
        elements = await self._collect_elements()

        scroll_x = await page.evaluate("() => window.scrollX || window.pageXOffset || 0")
        scroll_y = await page.evaluate("() => window.scrollY || window.pageYOffset || 0")

        network_summary = self._summarize_network()
        overlay = await self._detect_overlay()

        return Perception(
            url=page.url,
            title=await page.title(),
            viewport={
                "width": settings.window_width,
                "height": settings.window_height,
            },
            scroll_position={"x": scroll_x, "y": scroll_y},
            accessibility_tree=self._trim_tree(accessibility),
            elements=elements,
            console_logs=self._console_logs[-20:],
            network_summary=network_summary,
            page_errors=self._page_errors[-10:],
            overlay=overlay,
        )

    async def _collect_elements(self) -> list[ElementHint]:
        js = """
        () => {
            const roles = ['a', 'button', 'input', 'textarea', 'select', 'summary', 'label', '[role="button"]', '[role="link"]', '[role="textbox"]', '[role="searchbox"]', '[role="checkbox"]', '[role="radio"]', '[role="tab"]', '[role="menuitem"]'];
            const seen = new Set();
            const hints = [];
            let index = 0;
            for (const sel of roles) {
                for (const el of document.querySelectorAll(sel)) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    if (seen.has(el)) continue;
                    seen.add(el);
                    const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || '').trim().slice(0, 120);
                    const role = el.getAttribute('role') || el.tagName.toLowerCase();
                    const selector = el.id ? '#' + el.id : Array.from(el.classList).map(c => '.' + c).join('') || sel;
                    hints.push({
                        index: index++,
                        role: role,
                        text: text,
                        selector: selector,
                        box: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
                    });
                }
            }
            return hints.slice(0, 100);
        }
        """
        raw = await self.page.evaluate(js)
        return [
            ElementHint(
                index=item["index"],
                role=item["role"],
                text=item["text"],
                selector=item["selector"],
                box=BoundingBox(**item["box"]),
            )
            for item in raw
        ]

    async def _detect_overlay(self) -> dict | None:
        """Detect a visible modal, cookie banner, or other blocking overlay."""
        js = """
        () => {
            const candidates = [
                ...document.querySelectorAll('[role="dialog"], [aria-modal="true"]'),
                ...document.querySelectorAll('.modal, .overlay, .popup, .toast'),
                ...document.querySelectorAll('div[class*="cookie"], div[id*="cookie"]'),
                ...document.querySelectorAll('div[class*="consent"], div[id*="consent"]'),
                ...document.querySelectorAll('div[class*="banner"], div[id*="banner"]'),
            ];
            const viewportW = window.innerWidth;
            const viewportH = window.innerHeight;
            for (const el of candidates) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (rect.width < 100 || rect.height < 40) continue;
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                // Consider it blocking if it covers a meaningful portion of the viewport center.
                const visible = Math.min(rect.right, viewportW) - Math.max(rect.left, 0);
                if (visible < viewportW * 0.3) continue;
                const text = (el.innerText || '').trim().slice(0, 160);
                const dismissSelectors = [
                    'button[class*="close"]', 'button[aria-label*="close" i]', 'button[title*="close" i]',
                    '[data-testid*="close" i]', '[data-testid*="dismiss" i]', '[data-testid*="reject" i]',
                    'button:has-text("Maybe later")', 'button:has-text("No thanks")', 'button:has-text("Reject")',
                    'button:has-text("Close")', 'button:has-text("Dismiss")', 'button:has-text("Skip")',
                    '.fc-close-icon', '.banner-close', '.ot-close-icon', 'button.onetrust-close-btn-handler',
                ];
                const dismiss = [];
                for (const sel of dismissSelectors) {
                    try {
                        for (const btn of el.querySelectorAll(sel)) {
                            const brect = btn.getBoundingClientRect();
                            if (brect.width && brect.height) {
                                dismiss.push({
                                    text: (btn.innerText || btn.getAttribute('aria-label') || '').trim().slice(0, 40),
                                    selector: sel,
                                });
                            }
                        }
                    } catch (e) {}
                }
                return {
                    tag: el.tagName,
                    class: el.className || null,
                    text: text,
                    box: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                    dismiss_candidates: dismiss.slice(0, 5),
                };
            }
            return null;
        }
        """
        return await self.page.evaluate(js)

    def _summarize_network(self) -> dict:
        failed = [r for r in self._network_requests if r.get("event") == "response" and r.get("status", 0) >= 400]
        pending = [r for r in self._network_requests if r.get("event") == "request"]
        completed = [r for r in self._network_requests if r.get("event") == "response"]
        return {
            "completed": len(completed),
            "pending": len(pending) - len(completed),
            "failed_4xx_5xx": len(failed),
            "recent_errors": [
                {"url": r.get("url"), "status": r.get("status")}
                for r in failed[-5:]
            ],
        }

    def _trim_tree(self, node: dict | None, depth: int = 0) -> dict | None:
        if not node or depth > 4:
            return None
        trimmed: dict[str, Any] = {}
        for key in ("role", "name", "value", "checked", "pressed", "selected"):
            if key in node:
                trimmed[key] = node[key]
        children = node.get("children") or []
        if children:
            trimmed["children"] = [
                child for child in (self._trim_tree(c, depth + 1) for c in children) if child
            ][:30]
        return trimmed

    async def execute(self, action: Action, perception: Perception | None = None) -> Step:
        start_cursor = Point(self._cursor.x, self._cursor.y)
        target_box: BoundingBox | None = None
        target_label = action.selector or action.text or action.url
        error: str | None = None
        before: bytes | None = None
        after: bytes | None = None
        extract_result: dict | None = None

        try:
            if action.type not in ("screenshot", "finish", "fail", "extract"):
                await self._page.wait_for_timeout(settings.action_delay_ms)
            before = await self.screenshot()
            # Auto-dismiss known popups before click/type/hover if no explicit target provided.
            if action.type in ("click", "hover", "type") and not any([action.element_index is not None, action.selector, action.x is not None]):
                await self._try_dismiss_popups()
            target_box = await self._resolve_target_box(action, perception)
            if target_box:
                idx = action.element_index
                if idx is not None:
                    target_label = f"element[{idx}]"
            if action.type == "extract":
                extract_result = await self._extract(action.extract_schema)
            else:
                await self._perform(action, target_box)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"

        if error is None:
            await self._page.wait_for_timeout(settings.wait_after_action_ms)

        try:
            after = await self.screenshot()
        except Exception:  # noqa: BLE001
            if before is not None:
                after = before

        return Step(
            number=-1,  # filled in by agent
            action=action,
            before_screenshot_b64=Step.b64_from_bytes(before),
            after_screenshot_b64=Step.b64_from_bytes(after),
            cursor_start=start_cursor,
            cursor_end=Point(self._cursor.x, self._cursor.y),
            target_box=target_box,
            target_label=target_label,
            perception=perception,
            error=error,
            extract_result=extract_result,
        )

    async def _resolve_target_box(
        self, action: Action, perception: Perception | None = None
    ) -> BoundingBox | None:
        if action.element_index is not None and perception:
            for el in perception.elements:
                if el.index == action.element_index:
                    return el.box
        if action.x is not None and action.y is not None:
            return BoundingBox(x=action.x - 5, y=action.y - 5, width=10, height=10)
        if action.selector:
            locator = self.page.locator(action.selector).first
            if await locator.is_visible(timeout=settings.action_timeout_ms):
                box = await locator.bounding_box()
                if box:
                    return BoundingBox(**box)
        if action.text:
            locator = self.page.get_by_text(action.text, exact=False).first
            if await locator.is_visible(timeout=settings.action_timeout_ms):
                box = await locator.bounding_box()
                if box:
                    return BoundingBox(**box)
        return None

    async def _perform(self, action: Action, box: BoundingBox | None) -> None:
        p = self._resolve_point(action, box)

        if action.type == "goto":
            url = action.url or "about:blank"
            await self.page.goto(url, wait_until="domcontentloaded")
            return

        if action.type == "click":
            await self._move_cursor_to(p)
            await self.page.wait_for_timeout(settings.settle_delay_ms)
            await self.page.mouse.click(p.x, p.y)
            return

        if action.type == "hover":
            await self._move_cursor_to(p)
            await self.page.wait_for_timeout(settings.settle_delay_ms)
            return

        if action.type == "type":
            if action.selector or action.text:
                locator = self._resolve_locator(action)
                await locator.click()
                await self.page.wait_for_timeout(settings.settle_delay_ms)
                if action.text:
                    await locator.fill(action.text, timeout=settings.action_timeout_ms)
            else:
                await self._move_cursor_to(p)
                await self.page.mouse.click(p.x, p.y)
                await self.page.wait_for_timeout(settings.settle_delay_ms)
                if action.text:
                    await self.page.keyboard.type(action.text)
            return

        if action.type == "scroll":
            amount = action.amount or 300
            if action.direction == "up":
                amount = -amount
            await self.page.mouse.wheel(0, amount)
            return

        if action.type == "wait":
            duration = action.duration_ms or 1000
            await self.page.wait_for_timeout(duration)
            return

        if action.type == "keypress":
            key = action.key or action.text or ""
            await self.page.keyboard.press(key)
            return

        if action.type == "keydown":
            key = action.key or action.text or ""
            await self.page.keyboard.down(key)
            return

        if action.type == "keyup":
            key = action.key or action.text or ""
            await self.page.keyboard.up(key)
            return

        if action.type == "mousedown":
            await self._move_cursor_to(p)
            await self.page.mouse.down()
            return

        if action.type == "mouseup":
            await self._move_cursor_to(p)
            await self.page.mouse.up()
            return

        if action.type == "drag":
            await self._move_cursor_to(p)
            await self.page.mouse.down()
            end = Point(
                action.end_x if action.end_x is not None else p.x,
                action.end_y if action.end_y is not None else p.y,
            )
            await self._move_cursor_to(end)
            await self.page.mouse.up()
            return

        if action.type == "screenshot":
            return

        if action.type == "extract":
            return

        if action.type == "dismiss_popup":
            dismissed = await self._try_dismiss_popups()
            if not dismissed:
                raise RuntimeError("No dismissable overlay found")
            return

        if action.type == "human_input":
            return

        if action.type in ("finish", "fail"):
            return

        raise ValueError(f"Unsupported action type: {action.type}")

    def _resolve_point(self, action: Action, box: BoundingBox | None) -> Point:
        if action.x is not None and action.y is not None:
            return Point(action.x, action.y)
        if box:
            return Point(
                x=box.x + box.width / 2,
                y=box.y + box.height / 2,
            )
        return Point(self._cursor.x, self._cursor.y)

    def _resolve_locator(self, action: Action) -> Locator:
        if action.selector:
            return self.page.locator(action.selector).first
        if action.text:
            return self.page.get_by_text(action.text, exact=False).first
        raise ValueError("Action needs selector or text to resolve a locator")

    async def _try_dismiss_popups(self) -> bool:
        """Try common dismiss patterns for cookie banners, sign-in modals, and overlays."""
        selectors = [
            'button:has-text("Reject additional cookies")',
            'button:has-text("Maybe later")',
            'button:has-text("No thanks")',
            'button:has-text("Dismiss")',
            'button:has-text("Close")',
            'button:has-text("Skip")',
            'button:has-text("Got it")',
            'button:has-text("Accept")',
            'button[aria-label*="close" i]',
            'button[title*="close" i]',
            '[data-testid*="close" i]',
            '[data-testid*="dismiss" i]',
            '[data-testid*="reject" i]',
            '.fc-close-icon',
            '.ot-close-icon',
            'button.onetrust-close-btn-handler',
            'button[aria-label*="dismiss" i]',
        ]
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=200):
                    box = await loc.bounding_box()
                    if box and box["width"] and box["height"]:
                        await self._move_cursor_to(Point(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2))
                        await self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        await self.page.wait_for_timeout(400)
                        return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def _extract(self, schema: dict[str, Any] | None) -> dict[str, Any]:
        """Extract structured data from the current page using a JSON schema."""
        if not schema:
            return {}

        js = """
        (schema) => {
            const extractSpec = (spec, ctx) => {
                if (typeof spec === 'string') spec = { selector: spec };
                const selector = spec.selector;
                if (!selector) return null;
                const attr = spec.attribute || spec.attr || 'text';
                const multiple = spec.multiple === true;
                const fields = spec.fields;
                const elements = Array.from(ctx.querySelectorAll(selector));

                if (attr === 'count') return elements.length;
                if (attr === 'exists') return elements.length > 0;

                const extractOne = (el) => {
                    if (fields) {
                        const out = {};
                        for (const [key, fieldSpec] of Object.entries(fields)) {
                            out[key] = extractSpec(fieldSpec, el);
                        }
                        return out;
                    }
                    if (attr === 'text') return (el.innerText || '').trim();
                    if (attr === 'html') return el.innerHTML || '';
                    return el.getAttribute(attr) || '';
                };

                if (multiple) return elements.map(extractOne);
                if (elements.length === 0) return null;
                return extractOne(elements[0]);
            };

            const result = {};
            for (const [key, spec] of Object.entries(schema)) {
                result[key] = extractSpec(spec, document);
            }
            return result;
        }
        """
        return cast(dict[str, Any], await self.page.evaluate(js, schema))

    async def _move_cursor_to(self, target: Point) -> None:
        # Clamp to viewport
        target.x = max(0, min(settings.window_width, target.x))
        target.y = max(0, min(settings.window_height, target.y))

        start = Point(self._cursor.x, self._cursor.y)
        distance = ((target.x - start.x) ** 2 + (target.y - start.y) ** 2) ** 0.5
        if distance < 1:
            self._cursor = target
            return

        # Smooth movement: ~30 px/ms, 16 ms frame budget, capped at 40 frames.
        duration_ms = min(max(distance / 2, 200), 700)
        frames = max(3, int(duration_ms / 16))
        for i in range(1, frames + 1):
            t = i / frames
            # Ease-out cubic for natural deceleration
            ease = 1 - (1 - t) ** 3
            self._cursor.x = start.x + (target.x - start.x) * ease
            self._cursor.y = start.y + (target.y - start.y) * ease
            await self.page.mouse.move(self._cursor.x, self._cursor.y)
            await self.page.wait_for_timeout(16)

        self._cursor = target
