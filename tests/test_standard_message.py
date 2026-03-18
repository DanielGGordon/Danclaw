"""Tests for the StandardMessage data model."""

import json

import pytest

from dispatcher.models import StandardMessage


class TestStandardMessageCreation:
    """Test creating StandardMessage instances."""

    def test_create_with_all_fields(self):
        msg = StandardMessage(
            source="slack",
            channel_ref="C123/1234567890.123456",
            user_id="U456",
            content="Hello, agent!",
            session_id="sess-001",
        )
        assert msg.source == "slack"
        assert msg.channel_ref == "C123/1234567890.123456"
        assert msg.user_id == "U456"
        assert msg.content == "Hello, agent!"
        assert msg.session_id == "sess-001"

    def test_session_id_defaults_to_none(self):
        msg = StandardMessage(
            source="terminal",
            channel_ref="fd:3",
            user_id="dgordon",
            content="Hi",
        )
        assert msg.session_id is None

    def test_empty_content_is_allowed(self):
        msg = StandardMessage(
            source="terminal",
            channel_ref="fd:3",
            user_id="dgordon",
            content="",
        )
        assert msg.content == ""


class TestStandardMessageImmutability:
    """Test that StandardMessage is frozen (immutable)."""

    def test_cannot_change_source(self):
        msg = StandardMessage(
            source="slack",
            channel_ref="C123",
            user_id="U1",
            content="test",
        )
        with pytest.raises(AttributeError):
            msg.source = "terminal"

    def test_cannot_change_content(self):
        msg = StandardMessage(
            source="slack",
            channel_ref="C123",
            user_id="U1",
            content="original",
        )
        with pytest.raises(AttributeError):
            msg.content = "modified"

    def test_cannot_change_session_id(self):
        msg = StandardMessage(
            source="slack",
            channel_ref="C123",
            user_id="U1",
            content="test",
        )
        with pytest.raises(AttributeError):
            msg.session_id = "new-session"


class TestStandardMessageSerialization:
    """Test to_dict and from_dict for JSON transport."""

    def test_to_dict_all_fields(self):
        msg = StandardMessage(
            source="slack",
            channel_ref="C123",
            user_id="U456",
            content="hello",
            session_id="sess-001",
        )
        d = msg.to_dict()
        assert d == {
            "source": "slack",
            "channel_ref": "C123",
            "user_id": "U456",
            "content": "hello",
            "session_id": "sess-001",
        }

    def test_to_dict_none_session_id(self):
        msg = StandardMessage(
            source="terminal",
            channel_ref="fd:3",
            user_id="dgordon",
            content="hi",
        )
        d = msg.to_dict()
        assert d["session_id"] is None

    def test_from_dict_all_fields(self):
        data = {
            "source": "twilio",
            "channel_ref": "+15551234567",
            "user_id": "caller-001",
            "content": "What's the weather?",
            "session_id": "sess-xyz",
        }
        msg = StandardMessage.from_dict(data)
        assert msg.source == "twilio"
        assert msg.channel_ref == "+15551234567"
        assert msg.user_id == "caller-001"
        assert msg.content == "What's the weather?"
        assert msg.session_id == "sess-xyz"

    def test_from_dict_without_session_id(self):
        data = {
            "source": "terminal",
            "channel_ref": "fd:3",
            "user_id": "dgordon",
            "content": "hi",
        }
        msg = StandardMessage.from_dict(data)
        assert msg.session_id is None

    def test_roundtrip(self):
        original = StandardMessage(
            source="slack",
            channel_ref="C999/ts",
            user_id="U001",
            content="round trip test",
            session_id="sess-rt",
        )
        restored = StandardMessage.from_dict(original.to_dict())
        assert restored == original

    def test_roundtrip_through_json(self):
        original = StandardMessage(
            source="slack",
            channel_ref="C999/ts",
            user_id="U001",
            content="json round trip",
            session_id="sess-json",
        )
        json_str = json.dumps(original.to_dict())
        restored = StandardMessage.from_dict(json.loads(json_str))
        assert restored == original

    def test_extra_keys_ignored(self):
        data = {
            "source": "slack",
            "channel_ref": "C1",
            "user_id": "U1",
            "content": "hi",
            "extra_field": "should be ignored",
        }
        msg = StandardMessage.from_dict(data)
        assert msg.source == "slack"
        assert not hasattr(msg, "extra_field")


class TestStandardMessageValidation:
    """Test from_dict validation of required fields and types."""

    @pytest.mark.parametrize("missing_field", [
        "source", "channel_ref", "user_id", "content",
    ])
    def test_missing_required_field(self, missing_field):
        data = {
            "source": "slack",
            "channel_ref": "C1",
            "user_id": "U1",
            "content": "hi",
        }
        del data[missing_field]
        with pytest.raises(TypeError, match=f"missing required.*{missing_field}"):
            StandardMessage.from_dict(data)

    def test_missing_multiple_fields(self):
        with pytest.raises(TypeError, match="missing required"):
            StandardMessage.from_dict({"content": "hi"})

    @pytest.mark.parametrize("field", [
        "source", "channel_ref", "user_id", "content",
    ])
    def test_non_string_required_field(self, field):
        data = {
            "source": "slack",
            "channel_ref": "C1",
            "user_id": "U1",
            "content": "hi",
        }
        data[field] = 42
        with pytest.raises(TypeError, match=f"'{field}' must be a str"):
            StandardMessage.from_dict(data)

    def test_non_string_session_id(self):
        data = {
            "source": "slack",
            "channel_ref": "C1",
            "user_id": "U1",
            "content": "hi",
            "session_id": 123,
        }
        with pytest.raises(TypeError, match="'session_id' must be a str or None"):
            StandardMessage.from_dict(data)


class TestStandardMessageEquality:
    """Test equality and hashing behavior."""

    def test_equal_messages(self):
        a = StandardMessage("slack", "C1", "U1", "hi", "s1")
        b = StandardMessage("slack", "C1", "U1", "hi", "s1")
        assert a == b

    def test_different_messages(self):
        a = StandardMessage("slack", "C1", "U1", "hi", "s1")
        b = StandardMessage("slack", "C1", "U1", "bye", "s1")
        assert a != b

    def test_hashable(self):
        msg = StandardMessage("slack", "C1", "U1", "hi")
        s = {msg}
        assert msg in s
