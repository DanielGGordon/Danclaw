"""Tests for the permission resolver."""

from __future__ import annotations

from config import ChannelPermissions, PermissionsConfig, UserPermissions
from dispatcher.permissions import requires_approval, resolve_permissions


class TestResolvePermissions:
    """Tests for resolve_permissions."""

    def test_channel_only(self) -> None:
        """Channel tools are returned when user has no extra permissions."""
        config = PermissionsConfig(
            channels={"slack": ChannelPermissions(allowed_tools=["git", "search"])},
            users={},
        )
        result = resolve_permissions(config, "slack", "unknown-user")
        assert result == frozenset({"git", "search"})

    def test_user_only(self) -> None:
        """User tools are returned when channel is unknown."""
        config = PermissionsConfig(
            channels={},
            users={"alice": UserPermissions(additional_tools=["deploy", "restart"])},
        )
        result = resolve_permissions(config, "unknown-channel", "alice")
        assert result == frozenset({"deploy", "restart"})

    def test_channel_user_union(self) -> None:
        """Channel and user tools are merged via union."""
        config = PermissionsConfig(
            channels={"slack": ChannelPermissions(allowed_tools=["git", "search"])},
            users={"alice": UserPermissions(additional_tools=["deploy", "search"])},
        )
        result = resolve_permissions(config, "slack", "alice")
        assert result == frozenset({"git", "search", "deploy"})

    def test_unknown_channel(self) -> None:
        """Unknown channel yields only user tools."""
        config = PermissionsConfig(
            channels={"slack": ChannelPermissions(allowed_tools=["git"])},
            users={"alice": UserPermissions(additional_tools=["deploy"])},
        )
        result = resolve_permissions(config, "nonexistent", "alice")
        assert result == frozenset({"deploy"})

    def test_unknown_user(self) -> None:
        """Unknown user yields only channel tools."""
        config = PermissionsConfig(
            channels={"slack": ChannelPermissions(allowed_tools=["git"])},
            users={"alice": UserPermissions(additional_tools=["deploy"])},
        )
        result = resolve_permissions(config, "slack", "nonexistent")
        assert result == frozenset({"git"})

    def test_empty_config(self) -> None:
        """Empty config returns an empty frozenset."""
        config = PermissionsConfig()
        result = resolve_permissions(config, "slack", "alice")
        assert result == frozenset()

    def test_channel_override_ignores_user(self) -> None:
        """When channel override is True, user tools are excluded."""
        config = PermissionsConfig(
            channels={
                "restricted": ChannelPermissions(
                    allowed_tools=["read_only"],
                    override=True,
                ),
            },
            users={"alice": UserPermissions(additional_tools=["deploy", "restart"])},
        )
        result = resolve_permissions(config, "restricted", "alice")
        assert result == frozenset({"read_only"})

    def test_channel_override_unknown_user(self) -> None:
        """Override channel with unknown user still returns channel tools."""
        config = PermissionsConfig(
            channels={
                "restricted": ChannelPermissions(
                    allowed_tools=["read_only"],
                    override=True,
                ),
            },
        )
        result = resolve_permissions(config, "restricted", "nobody")
        assert result == frozenset({"read_only"})

    def test_returns_frozenset(self) -> None:
        """Result is always a frozenset (immutable)."""
        config = PermissionsConfig(
            channels={"slack": ChannelPermissions(allowed_tools=["git"])},
        )
        result = resolve_permissions(config, "slack", "alice")
        assert isinstance(result, frozenset)

    def test_both_unknown(self) -> None:
        """Both unknown channel and user returns empty frozenset."""
        config = PermissionsConfig(
            channels={"slack": ChannelPermissions(allowed_tools=["git"])},
            users={"alice": UserPermissions(additional_tools=["deploy"])},
        )
        result = resolve_permissions(config, "nope", "nope")
        assert result == frozenset()


class TestRequiresApproval:
    """Tests for requires_approval."""

    def test_approval_from_channel(self) -> None:
        """Channel approval_required=True triggers approval."""
        config = PermissionsConfig(
            channels={
                "slack": ChannelPermissions(
                    allowed_tools=["git"],
                    approval_required=True,
                ),
            },
            users={"alice": UserPermissions(additional_tools=["deploy"])},
        )
        assert requires_approval(config, "slack", "alice") is True

    def test_approval_from_user(self) -> None:
        """User approval_required=True triggers approval."""
        config = PermissionsConfig(
            channels={"slack": ChannelPermissions(allowed_tools=["git"])},
            users={
                "alice": UserPermissions(
                    additional_tools=["deploy"],
                    approval_required=True,
                ),
            },
        )
        assert requires_approval(config, "slack", "alice") is True

    def test_approval_from_both(self) -> None:
        """Both channel and user approval_required=True triggers approval."""
        config = PermissionsConfig(
            channels={
                "slack": ChannelPermissions(
                    allowed_tools=["git"],
                    approval_required=True,
                ),
            },
            users={
                "alice": UserPermissions(
                    additional_tools=["deploy"],
                    approval_required=True,
                ),
            },
        )
        assert requires_approval(config, "slack", "alice") is True

    def test_approval_from_neither(self) -> None:
        """Neither channel nor user sets approval_required — returns False."""
        config = PermissionsConfig(
            channels={"slack": ChannelPermissions(allowed_tools=["git"])},
            users={"alice": UserPermissions(additional_tools=["deploy"])},
        )
        assert requires_approval(config, "slack", "alice") is False

    def test_approval_empty_config(self) -> None:
        """Empty config returns False."""
        config = PermissionsConfig()
        assert requires_approval(config, "slack", "alice") is False

    def test_approval_unknown_channel_and_user(self) -> None:
        """Unknown channel and user returns False."""
        config = PermissionsConfig(
            channels={
                "slack": ChannelPermissions(
                    allowed_tools=["git"],
                    approval_required=True,
                ),
            },
            users={
                "alice": UserPermissions(
                    additional_tools=["deploy"],
                    approval_required=True,
                ),
            },
        )
        assert requires_approval(config, "nope", "nope") is False

    def test_approval_override_channel_only(self) -> None:
        """Override channel: only channel approval_required matters.

        When override=True and channel has approval_required=True, returns True
        even though user does not require approval.
        """
        config = PermissionsConfig(
            channels={
                "restricted": ChannelPermissions(
                    allowed_tools=["read_only"],
                    override=True,
                    approval_required=True,
                ),
            },
            users={"alice": UserPermissions(additional_tools=["deploy"])},
        )
        assert requires_approval(config, "restricted", "alice") is True

    def test_approval_override_ignores_user(self) -> None:
        """Override channel: user approval_required is ignored.

        When override=True and channel has approval_required=False, returns
        False even though user has approval_required=True.
        """
        config = PermissionsConfig(
            channels={
                "restricted": ChannelPermissions(
                    allowed_tools=["read_only"],
                    override=True,
                    approval_required=False,
                ),
            },
            users={
                "alice": UserPermissions(
                    additional_tools=["deploy"],
                    approval_required=True,
                ),
            },
        )
        assert requires_approval(config, "restricted", "alice") is False

    def test_approval_unknown_user_channel_required(self) -> None:
        """Unknown user with channel approval_required=True returns True."""
        config = PermissionsConfig(
            channels={
                "slack": ChannelPermissions(
                    allowed_tools=["git"],
                    approval_required=True,
                ),
            },
        )
        assert requires_approval(config, "slack", "nobody") is True

    def test_approval_unknown_channel_user_required(self) -> None:
        """Unknown channel with user approval_required=True returns True."""
        config = PermissionsConfig(
            channels={},
            users={
                "alice": UserPermissions(
                    additional_tools=["deploy"],
                    approval_required=True,
                ),
            },
        )
        assert requires_approval(config, "unknown", "alice") is True
