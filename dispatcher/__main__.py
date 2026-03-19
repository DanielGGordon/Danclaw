"""Dispatcher entry point: load config, init DB, start SocketServer, wait for shutdown.

Starts the full dispatcher process:
1. Load and validate configuration.
2. Initialise the SQLite database schema.
3. Wire up Repository, SessionManager, MockExecutor, Dispatcher, and SocketServer.
4. Listen on a Unix domain socket for incoming messages.
5. Wait for SIGTERM/SIGINT, then shut down cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import aiosqlite

from config import load_config, ConfigError
from logging_config import setup_logging
from dispatcher.database import init_db
from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import MockExecutor
from dispatcher.repository import Repository
from dispatcher.session_manager import SessionManager
from dispatcher.socket_server import SocketServer
from dispatcher.telemetry import SlackLogSink, TelemetryCollector

logger = logging.getLogger("dispatcher")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "danclaw.json"
DEFAULT_SOCKET_PATH = os.environ.get("DANCLAW_SOCKET", "/tmp/danclaw.sock")
DEFAULT_DB_PATH = os.environ.get(
    "DANCLAW_DB", str(Path(__file__).resolve().parent.parent / "danclaw.db"),
)


def _setup_logging() -> None:
    """Configure root logging with structured JSON output."""
    setup_logging()


async def _run(
    config_path: Path,
    *,
    db_path: str | None = None,
    socket_path: str | None = None,
) -> None:
    """Main async loop: init DB, start SocketServer, wait for shutdown signal.

    Parameters
    ----------
    config_path:
        Path to the JSON configuration file.
    db_path:
        Filesystem path for the SQLite database.  Defaults to
        ``DEFAULT_DB_PATH``.
    socket_path:
        Filesystem path for the Unix domain socket.  Defaults to
        ``DEFAULT_SOCKET_PATH``.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    if socket_path is None:
        socket_path = DEFAULT_SOCKET_PATH

    config = load_config(config_path)
    agent_count = len(config.agents)
    agent_names = ", ".join(a.name for a in config.agents)
    logger.info(
        "Dispatcher ready — %d agent(s) loaded: %s",
        agent_count,
        agent_names,
    )

    # Build telemetry collector with configured sinks
    telemetry = TelemetryCollector()
    if config.telemetry.slack_log_channel:
        from slack_sdk import WebClient

        slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
        slack_client = WebClient(token=slack_token)
        slack_sink = SlackLogSink(slack_client, config.telemetry.slack_log_channel)
        telemetry.add_sink(slack_sink)
        logger.info(
            "SlackLogSink enabled for channel %s",
            config.telemetry.slack_log_channel,
        )

    # Initialise database schema
    await init_db(db_path)
    logger.info("Database initialised at %s", db_path)

    # Open a persistent connection for the lifetime of the process
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")

        # Wire up components
        repo = Repository(db)
        session_manager = SessionManager(repo)
        executor = MockExecutor()
        dispatcher = Dispatcher(session_manager, repo, executor, config=config, telemetry=telemetry)
        server = SocketServer(dispatcher, socket_path)

        # Start the socket server
        await server.start()
        logger.info("Socket server listening on %s", socket_path)

        # Set up signal-driven shutdown
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received")
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        try:
            await shutdown_event.wait()
        finally:
            await server.stop()
            logger.info("Dispatcher shut down cleanly")


def main(
    config_path: Path | None = None,
    *,
    db_path: str | None = None,
    socket_path: str | None = None,
) -> None:
    """Set up logging and run the async event loop.

    Parameters
    ----------
    config_path:
        Path to the JSON config file.  Defaults to ``DEFAULT_CONFIG_PATH``.
    db_path:
        Path to the SQLite database file.
    socket_path:
        Path to the Unix domain socket.
    """
    _setup_logging()

    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    try:
        asyncio.run(_run(config_path, db_path=db_path, socket_path=socket_path))
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    _config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    main(config_path=_config_path)
