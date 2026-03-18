"""CLI entry point for the ``agent`` command.

Subcommands
-----------
``agent chat``
    Start an interactive chat session over the dispatcher's Unix domain socket.
    Each message is sent as a :class:`StandardMessage` JSON line.  The chat
    loop continues until the user types "exit" or presses Ctrl+C.

Usage::

    python -m cli.agent chat [--socket /path/to/socket]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import uuid


DEFAULT_SOCKET_PATH = os.environ.get(
    "DANCLAW_SOCKET", "/tmp/danclaw.sock"
)

USER_ID = os.environ.get("USER", "terminal-user")


def _build_message(content: str, session_id: str | None = None) -> dict:
    """Build a StandardMessage dict for the terminal source."""
    msg = {
        "source": "terminal",
        "channel_ref": f"cli-{uuid.uuid4().hex[:8]}",
        "user_id": USER_ID,
        "content": content,
    }
    if session_id is not None:
        msg["session_id"] = session_id
    return msg


def _connect(socket_path: str) -> socket.socket:
    """Open a blocking Unix domain socket connection.

    Raises
    ------
    ConnectionError
        If the socket file does not exist or the connection is refused.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        sock.close()
        raise ConnectionError(
            f"Cannot connect to dispatcher at {socket_path}: {exc}"
        ) from exc
    return sock


def _send_recv(sock: socket.socket, message: dict) -> dict:
    """Send a JSON line and read back the JSON response.

    Parameters
    ----------
    sock:
        A connected Unix domain socket.
    message:
        A dict representing the StandardMessage.

    Returns
    -------
    dict
        Parsed JSON response from the dispatcher.

    Raises
    ------
    ConnectionError
        If the server closes the connection unexpectedly.
    """
    line = json.dumps(message) + "\n"
    sock.sendall(line.encode("utf-8"))

    # Read response until newline
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Server closed the connection")
        buf += chunk
        if b"\n" in buf:
            break

    response_line = buf.split(b"\n", 1)[0]
    return json.loads(response_line)


def chat(socket_path: str, *, input_fn=None, print_fn=None) -> None:
    """Run the interactive chat loop.

    Parameters
    ----------
    socket_path:
        Path to the dispatcher's Unix domain socket.
    input_fn:
        Callable for reading user input (default: builtin ``input``).
        Accepts a prompt string and returns the user's input.
    print_fn:
        Callable for printing output (default: builtin ``print``).
    """
    if input_fn is None:
        input_fn = input
    if print_fn is None:
        print_fn = print

    sock = _connect(socket_path)
    # Use a stable channel_ref for the whole session so the dispatcher
    # can bind consecutive messages to the same session.
    channel_ref = f"cli-{uuid.uuid4().hex[:8]}"
    session_id: str | None = None

    print_fn(f"Connected to dispatcher at {socket_path}")
    print_fn('Type a message and press Enter. Type "exit" or press Ctrl+C to quit.\n')

    try:
        while True:
            try:
                user_input = input_fn("you> ")
            except EOFError:
                print_fn("")
                break

            if user_input.strip().lower() == "exit":
                print_fn("Goodbye.")
                break

            if not user_input.strip():
                continue

            msg = {
                "source": "terminal",
                "channel_ref": channel_ref,
                "user_id": USER_ID,
                "content": user_input,
            }
            if session_id is not None:
                msg["session_id"] = session_id

            try:
                resp = _send_recv(sock, msg)
            except ConnectionError as exc:
                print_fn(f"\nConnection lost: {exc}")
                break

            if resp.get("ok"):
                session_id = resp.get("session_id")
                print_fn(f"agent> {resp['response']}\n")
            else:
                print_fn(f"error> {resp.get('error', 'unknown error')}\n")
    except KeyboardInterrupt:
        print_fn("\nGoodbye.")
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and dispatch to the appropriate subcommand."""
    parser = argparse.ArgumentParser(
        prog="agent",
        description="CLI for interacting with the DanClaw dispatcher.",
    )
    subparsers = parser.add_subparsers(dest="command")

    chat_parser = subparsers.add_parser(
        "chat",
        help="Start an interactive chat session with the dispatcher.",
    )
    chat_parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET_PATH,
        help=f"Path to the dispatcher Unix domain socket (default: {DEFAULT_SOCKET_PATH})",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "chat":
        chat(args.socket)


if __name__ == "__main__":
    main()
