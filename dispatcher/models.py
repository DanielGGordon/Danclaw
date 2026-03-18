"""Data models for the dispatcher module.

Defines the StandardMessage — the universal internal message format used
for all communication between listeners and the dispatcher.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class StandardMessage:
    """Universal internal message format.

    Every listener converts its native format (Slack event, terminal input,
    Twilio webhook, etc.) into a StandardMessage before sending it to the
    dispatcher.  The dispatcher never sees channel-specific data.

    Attributes:
        source: Origin channel type (e.g. "terminal", "slack", "twilio").
        channel_ref: Channel-specific reference for routing responses back
            (e.g. Slack thread_ts, terminal session fd).
        user_id: Identifier for the user who sent the message.
        content: The message body text.
        session_id: Existing session ID to resume, or None for a new session.
    """

    source: str
    channel_ref: str
    user_id: str
    content: str
    session_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON transport."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StandardMessage:
        """Deserialize from a plain dict.

        Raises:
            TypeError: If required fields are missing.
            TypeError: If field values have incorrect types.
        """
        required = ("source", "channel_ref", "user_id", "content")
        missing = [f for f in required if f not in data]
        if missing:
            raise TypeError(
                f"StandardMessage missing required field(s): {', '.join(missing)}"
            )

        for field in required:
            if not isinstance(data[field], str):
                raise TypeError(
                    f"StandardMessage field '{field}' must be a str, "
                    f"got {type(data[field]).__name__}"
                )

        session_id = data.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise TypeError(
                f"StandardMessage field 'session_id' must be a str or None, "
                f"got {type(session_id).__name__}"
            )

        return cls(
            source=data["source"],
            channel_ref=data["channel_ref"],
            user_id=data["user_id"],
            content=data["content"],
            session_id=session_id,
        )
