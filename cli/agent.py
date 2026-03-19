"""CLI entry point for the ``agent`` command.

Subcommands
-----------
``agent chat``
    Start an interactive chat session over the dispatcher's Unix domain socket.
    Each message is sent as a :class:`StandardMessage` JSON line.  The chat
    loop continues until the user types "exit" or presses Ctrl+C.

``agent list``
    List all sessions from the dispatcher.  Connects to the Unix domain
    socket, sends a ``list_sessions`` request, and displays the results as
    a formatted table.

``agent attach <session-id>``
    Attach to an existing session.  Retrieves and displays the message
    history, then enters the chat loop bound to that session.

Usage::

    python -m cli.agent chat [--socket /path/to/socket]
    python -m cli.agent list [--socket /path/to/socket]
    python -m cli.agent attach <session-id> [--socket /path/to/socket]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import select
import socket
import sys
import threading
import uuid

from logging_config import setup_logging


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


def _read_json_line(sock: socket.socket, buf: bytearray) -> dict:
    """Read a single newline-delimited JSON object from *sock*.

    Uses *buf* as a carry-over buffer for partial reads.  On return,
    *buf* contains any bytes remaining after the first complete line.

    Raises
    ------
    ConnectionError
        If the server closes the connection before a complete line.
    """
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Server closed the connection")
        buf.extend(chunk)

    idx = buf.index(b"\n")
    line = bytes(buf[:idx])
    del buf[:idx + 1]
    return json.loads(line)


def _send_recv(
    sock: socket.socket,
    message: dict,
    *,
    print_fn=None,
    buf: bytearray | None = None,
) -> dict:
    """Send a JSON line and read back the JSON response.

    Any server-pushed fanout messages received before the actual response
    are printed via *print_fn* and skipped.  The first non-fanout JSON
    line is returned as the response.

    Parameters
    ----------
    sock:
        A connected Unix domain socket.
    message:
        A dict representing the StandardMessage or control request.
    print_fn:
        Callable for printing fanout messages (default: builtin ``print``).
    buf:
        Shared byte buffer for the connection.  If ``None``, a temporary
        buffer is used (fine for one-shot connections).

    Returns
    -------
    dict
        Parsed JSON response from the dispatcher.

    Raises
    ------
    ConnectionError
        If the server closes the connection unexpectedly.
    """
    if print_fn is None:
        print_fn = print
    if buf is None:
        buf = bytearray()

    line = json.dumps(message) + "\n"
    sock.sendall(line.encode("utf-8"))

    while True:
        resp = _read_json_line(sock, buf)
        if resp.get("type") == "fanout":
            _print_fanout(resp, print_fn)
            continue
        return resp


def _print_fanout(msg: dict, print_fn) -> None:
    """Print a fanout push message to the terminal."""
    source = msg.get("source", "unknown")
    response = msg.get("response", "")
    if response:
        print_fn(f"[{source}] agent> {response}\n")


def _fanout_reader(
    sock: socket.socket,
    buf: bytearray,
    print_fn,
    stop_event: threading.Event,
) -> None:
    """Background thread that reads fanout push messages from the server.

    Runs until *stop_event* is set.  Only reads when data is available
    (via ``select``), so it won't block shutdown.
    """
    while not stop_event.is_set():
        try:
            readable, _, _ = select.select([sock], [], [], 0.2)
        except (ValueError, OSError):
            # Socket closed
            break
        if not readable:
            continue
        try:
            chunk = sock.recv(4096)
        except (ConnectionResetError, OSError):
            break
        if not chunk:
            break
        buf.extend(chunk)
        # Process any complete lines in the buffer
        while b"\n" in buf:
            idx = buf.index(b"\n")
            line = bytes(buf[:idx])
            del buf[:idx + 1]
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if msg.get("type") == "fanout":
                _print_fanout(msg, print_fn)


def _chat_loop(
    sock,
    *,
    session_id: str | None = None,
    input_fn=None,
    print_fn=None,
) -> str:
    """Run the interactive chat read-eval-print loop.

    Starts a background reader thread that prints fanout push messages
    from the server (e.g. when a Slack message triggers a response on
    a bridged session).

    Parameters
    ----------
    sock:
        A connected Unix domain socket.
    session_id:
        If provided, messages are bound to this session.  Updated with
        the server's returned session_id after the first exchange.
    input_fn:
        Callable for reading user input (default: builtin ``input``).
    print_fn:
        Callable for printing output (default: builtin ``print``).

    Returns
    -------
    str
        The channel_ref used during this chat loop, for detach purposes.
    """
    if input_fn is None:
        input_fn = input
    if print_fn is None:
        print_fn = print

    channel_ref = f"cli-{uuid.uuid4().hex[:8]}"
    buf = bytearray()
    stop_event = threading.Event()
    reader_thread = threading.Thread(
        target=_fanout_reader,
        args=(sock, buf, print_fn, stop_event),
        daemon=True,
    )
    reader_thread.start()

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

            # Stop the reader while we do a synchronous send/recv
            stop_event.set()
            reader_thread.join(timeout=2)

            try:
                resp = _send_recv(sock, msg, print_fn=print_fn, buf=buf)
            except ConnectionError as exc:
                print_fn(f"\nConnection lost: {exc}")
                break

            if resp.get("ok"):
                session_id = resp.get("session_id")
                print_fn(f"agent> {resp['response']}\n")
            else:
                print_fn(f"error> {resp.get('error', 'unknown error')}\n")

            # Restart the reader thread
            stop_event.clear()
            reader_thread = threading.Thread(
                target=_fanout_reader,
                args=(sock, buf, print_fn, stop_event),
                daemon=True,
            )
            reader_thread.start()
    except KeyboardInterrupt:
        print_fn("\nGoodbye.")
    finally:
        stop_event.set()
        reader_thread.join(timeout=2)

    return channel_ref


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
    try:
        print_fn(f"Connected to dispatcher at {socket_path}")
        print_fn('Type a message and press Enter. Type "exit" or press Ctrl+C to quit.\n')
        _chat_loop(sock, input_fn=input_fn, print_fn=print_fn)
    finally:
        sock.close()


def _format_history(messages: list[dict]) -> str:
    """Format message history for display.

    Parameters
    ----------
    messages:
        Each dict must have ``role``, ``content``, ``source``, ``user_id``,
        and ``created_at`` keys.

    Returns
    -------
    str
        A formatted history string with one line per message, using
        ``you>`` for user messages and ``agent>`` for assistant messages.
        Returns an empty string if the list is empty.
    """
    if not messages:
        return ""

    lines: list[str] = []
    for m in messages:
        if m["role"] == "user":
            lines.append(f"you> {m['content']}")
        else:
            lines.append(f"agent> {m['content']}")
    return "\n".join(lines)


def attach(
    socket_path: str,
    session_id: str,
    *,
    input_fn=None,
    print_fn=None,
) -> None:
    """Attach to an existing session, display history, then enter chat loop.

    Parameters
    ----------
    socket_path:
        Path to the dispatcher's Unix domain socket.
    session_id:
        The ID of the session to attach to.
    input_fn:
        Callable for reading user input (default: builtin ``input``).
    print_fn:
        Callable for printing output (default: builtin ``print``).
    """
    if input_fn is None:
        input_fn = input
    if print_fn is None:
        print_fn = print

    sock = _connect(socket_path)
    try:
        # Fetch message history
        try:
            resp = _send_recv(sock, {
                "type": "get_history",
                "session_id": session_id,
            })
        except ConnectionError as exc:
            print_fn(f"Connection lost: {exc}")
            return

        if not resp.get("ok"):
            print_fn(f"Error: {resp.get('error', 'unknown error')}")
            return

        # Display history
        history = _format_history(resp.get("messages", []))
        if history:
            print_fn(f"--- Session {session_id} history ---")
            print_fn(history)
            print_fn(f"--- End of history ---\n")
        else:
            print_fn(f"Session {session_id} has no messages.\n")

        print_fn('Type a message and press Enter. Type "exit" or press Ctrl+C to quit.\n')
        channel_ref = _chat_loop(
            sock,
            session_id=session_id,
            input_fn=input_fn,
            print_fn=print_fn,
        )

        # Send detach request to remove the terminal binding
        if channel_ref is not None:
            try:
                _send_recv(sock, {
                    "type": "detach",
                    "session_id": session_id,
                    "channel_ref": channel_ref,
                })
            except ConnectionError:
                pass  # Server already gone — nothing to detach
    finally:
        sock.close()


def _format_sessions_table(sessions: list[dict]) -> str:
    """Format a list of session dicts as a text table.

    Parameters
    ----------
    sessions:
        Each dict must have ``id``, ``agent_name``, ``state``, and
        ``created_at`` keys.

    Returns
    -------
    str
        A formatted table string with header and one row per session.
        Returns a "No sessions found." message if the list is empty.
    """
    if not sessions:
        return "No sessions found."

    headers = ["ID", "AGENT", "STATE", "CREATED"]
    rows = [
        [s["id"], s["agent_name"], s["state"], s["created_at"]]
        for s in sessions
    ]

    # Compute column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def _fmt_row(cells: list[str]) -> str:
        return "  ".join(c.ljust(col_widths[i]) for i, c in enumerate(cells))

    lines = [_fmt_row(headers), _fmt_row(["-" * w for w in col_widths])]
    for row in rows:
        lines.append(_fmt_row(row))
    return "\n".join(lines)


def list_sessions(socket_path: str, *, print_fn=None) -> None:
    """Connect to the dispatcher and display all sessions.

    Parameters
    ----------
    socket_path:
        Path to the dispatcher's Unix domain socket.
    print_fn:
        Callable for printing output (default: builtin ``print``).
    """
    if print_fn is None:
        print_fn = print

    sock = _connect(socket_path)
    try:
        resp = _send_recv(sock, {"type": "list_sessions"})
    except ConnectionError as exc:
        print_fn(f"Connection lost: {exc}")
        return
    finally:
        sock.close()

    if not resp.get("ok"):
        print_fn(f"Error: {resp.get('error', 'unknown error')}")
        return

    print_fn(_format_sessions_table(resp.get("sessions", [])))


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

    list_parser = subparsers.add_parser(
        "list",
        help="List sessions from the dispatcher.",
    )
    list_parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET_PATH,
        help=f"Path to the dispatcher Unix domain socket (default: {DEFAULT_SOCKET_PATH})",
    )

    attach_parser = subparsers.add_parser(
        "attach",
        help="Attach to an existing session and show its history.",
    )
    attach_parser.add_argument(
        "session_id",
        help="ID of the session to attach to.",
    )
    attach_parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET_PATH,
        help=f"Path to the dispatcher Unix domain socket (default: {DEFAULT_SOCKET_PATH})",
    )

    args = parser.parse_args(argv)

    setup_logging(level=logging.WARNING)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "chat":
        chat(args.socket)
    elif args.command == "list":
        list_sessions(args.socket)
    elif args.command == "attach":
        attach(args.socket, args.session_id)


if __name__ == "__main__":
    main()
