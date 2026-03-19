"""Permission resolver: computes effective tool sets and approval gates for channel + user pairs."""

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


def requires_approval(
    config: PermissionsConfig,
    channel: str,
    user_id: str,
) -> bool:
    """Determine whether high-impact actions require approval.

    Returns ``True`` if *any* applicable permission layer (channel or user)
    has ``approval_required=True``.

    When the channel has ``override=True``, only the channel's
    ``approval_required`` flag is considered — the user's flag is ignored,
    just as the user's tools are ignored under override.

    Args:
        config: The :class:`PermissionsConfig` containing channel and user
            permission definitions.
        channel: The channel name to look up.
        user_id: The user identifier to look up.

    Returns:
        ``True`` if approval is required, ``False`` otherwise.
    """
    channel_perms = config.channels.get(channel)
    channel_approval = channel_perms.approval_required if channel_perms else False

    # If the channel has override set, user approval flag is ignored.
    if channel_perms and channel_perms.override:
        return channel_approval

    user_perms = config.users.get(user_id)
    user_approval = user_perms.approval_required if user_perms else False

    return channel_approval or user_approval
