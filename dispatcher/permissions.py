"""Permission resolver: computes effective tool sets for channel + user pairs."""

from __future__ import annotations

from config import PermissionsConfig


def resolve_permissions(
    config: PermissionsConfig,
    channel: str,
    user_id: str,
) -> frozenset[str]:
    """Compute the effective set of allowed tools for a channel + user.

    Logic:
        1. Start with the channel's ``allowed_tools`` (empty if channel unknown).
        2. If the channel has ``override=True``, return only the channel tools.
        3. Otherwise, add the user's ``additional_tools`` (union).

    Args:
        config: The :class:`PermissionsConfig` containing channel and user
            permission definitions.
        channel: The channel name to look up.
        user_id: The user identifier to look up.

    Returns:
        A frozen set of tool names the user is allowed to use on the channel.
    """
    channel_perms = config.channels.get(channel)
    channel_tools: set[str] = set(channel_perms.allowed_tools) if channel_perms else set()

    # If the channel has override set, user permissions are ignored.
    if channel_perms and channel_perms.override:
        return frozenset(channel_tools)

    user_perms = config.users.get(user_id)
    user_tools: set[str] = set(user_perms.additional_tools) if user_perms else set()

    return frozenset(channel_tools | user_tools)
