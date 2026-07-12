from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # No env_file is loaded by default. Pass configuration via CLI flags
    # or environment variables prefixed with BC_. This keeps the tool
    # terminal-driven and safe to invoke from another AI.
    model_config = SettingsConfigDict(
        env_prefix="BC_",
        extra="ignore",
    )

    # Browser (default headless so an AI can invoke it without a GUI)
    browser: str = "chromium"
    headless: bool = True
    window_width: int = 1920
    window_height: int = 1080
    slow_mo: int = 500  # ms Playwright sleeps between actions for visibility

    # Server / dashboard
    host: str = "127.0.0.1"
    port: int | None = None  # random high port is chosen at runtime if not set

    # Agent
    max_steps: int = 30
    action_timeout_ms: int = 10_000
    wait_after_action_ms: int = 600  # short pause after each action for the page to settle
    action_delay_ms: int = 200  # brief pause before each action so motion is visible
    settle_delay_ms: int = 150  # cursor rests on target briefly before clicking

    # Output directory for all results, reports, screenshots, and session recordings.
    # Must be provided via BC_OUTPUT_DIR or --output-dir; there is no default.
    output_dir: str | None = None


settings = Settings()


def require_output_dir() -> str:
    """Return the configured output directory or raise a clear error."""
    if not settings.output_dir:
        raise RuntimeError(
            "An output directory is required. Set BC_OUTPUT_DIR or pass --output-dir."
        )
    return settings.output_dir
