"""Dispatcher entry point: load config, log readiness, wait for shutdown signal."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from config import load_config, ConfigError

logger = logging.getLogger("dispatcher")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "danclaw.json"


def _setup_logging() -> None:
    """Configure root logging with a consistent format."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


async def _run(config_path: Path) -> None:
    """Main async loop: log readiness and wait for a shutdown signal."""
    config = load_config(config_path)
    agent_count = len(config.agents)
    agent_names = ", ".join(a.name for a in config.agents)
    logger.info(
        "Dispatcher ready — %d agent(s) loaded: %s",
        agent_count,
        agent_names,
    )

    # Create a future that will be resolved by the signal handler.
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    await shutdown_event.wait()
    logger.info("Dispatcher shut down cleanly")


def main(config_path: Path | None = None) -> None:
    """Set up logging and run the async event loop."""
    _setup_logging()

    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    try:
        asyncio.run(_run(config_path))
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
