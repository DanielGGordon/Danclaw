"""Tests for the permission resolver."""

from __future__ import annotations

from config import ChannelPermissions, PermissionsConfig, UserPermissions
from dispatcher.permissions import resolve_permissions


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
