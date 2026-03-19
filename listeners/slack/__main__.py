"""Entry point for running the Slack listener as a standalone process.

Usage::

    python -m listeners.slack [--socket-path /path/to/dispatcher.sock]

Requires ``SLACK_BOT_TOKEN`` and ``SLACK_APP_TOKEN`` environment variables.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from listeners.slack.listener import SlackListener

DEFAULT_SOCKET_PATH = "/tmp/danclaw-dispatcher.sock"


def main() -> None:
    """Parse arguments and run the Slack listener."""
    parser = argparse.ArgumentParser(
        description="DanClaw Slack listener (Socket Mode)",
    )
    parser.add_argument(
        "--socket-path",
        default=DEFAULT_SOCKET_PATH,
        help=f"Path to dispatcher Unix socket (default: {DEFAULT_SOCKET_PATH})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    listener = SlackListener(socket_path=args.socket_path)

    def _shutdown(signum, frame):
        logging.getLogger(__name__).info("Received signal %s, shutting down", signum)
        listener.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    listener.start()


if __name__ == "__main__":
    main()
