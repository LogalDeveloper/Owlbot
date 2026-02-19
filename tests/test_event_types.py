# Copyright 2026 Logan Fick
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for Owncast webhook event types, timestamp parsing, and event logging.

Tests exercise the pure-parsing layer in owlbot.api.event_types without
needing network or database access.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from owlbot.api.event_types import (
    ChatEvent,
    Event,
    EventType,
    NameChangedEvent,
    StreamStartedEvent,
    StreamStatus,
    StreamStoppedEvent,
    StreamTitleUpdatedEvent,
    User,
    UserJoinedEvent,
    UserPartedEvent,
    VisibilityUpdateEvent,
    _parse_timestamp,
    log_event,
    parse_event,
)


def _user_dict(
    *,
    user_id: str = "abc123",
    display_name: str = "Alice",
    display_color: int = 3,
    created_at: str = "2025-06-01T10:00:00Z",
    previous_names: list[str] | None = None,
    name_changed_at: str = "2025-07-01T12:00:00Z",
    is_bot: bool = False,
    authenticated: bool = True,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Build a realistic Owncast user dict."""
    return {
        "id": user_id,
        "displayName": display_name,
        "displayColor": display_color,
        "createdAt": created_at,
        "previousNames": previous_names if previous_names is not None else ["OldAlice"],
        "nameChangedAt": name_changed_at,
        "isBot": is_bot,
        "authenticated": authenticated,
        "scopes": scopes if scopes is not None else ["MODERATOR"],
    }


def _chat_event_dict(
    *,
    body: str = "<p>Hello everyone!</p>",
    raw_body: str = "<p>Hello everyone!</p>",
    user: dict[str, Any] | None = None,
    client_id: int = 42,
    message_id: str = "msg-001",
    visible: bool = True,
    timestamp: str = "2026-01-15T12:00:00Z",
) -> dict[str, Any]:
    """Build a realistic Owncast CHAT eventData dict."""
    return {
        "body": body,
        "rawBody": raw_body,
        "user": user if user is not None else _user_dict(),
        "clientId": client_id,
        "id": message_id,
        "visible": visible,
        "timestamp": timestamp,
    }


def _stream_event_dict(
    *,
    server_id: str = "srv-001",
    server_name: str = "My Stream",
    stream_title: str = "Playing Minecraft",
    summary: str = "A fun stream",
    timestamp: str = "2026-01-15T18:00:00Z",
) -> dict[str, Any]:
    """Build a realistic Owncast stream started/stopped eventData dict."""
    return {
        "id": server_id,
        "name": server_name,
        "streamTitle": stream_title,
        "summary": summary,
        "timestamp": timestamp,
    }


def _stream_status_dict(
    *,
    last_connect_time: str = "2026-01-15T17:00:00Z",
    last_disconnect_time: str = "0001-01-01T00:00:00Z",
    version_number: str = "0.2.0",
    stream_title: str = "Playing Minecraft",
    viewer_count: int = 15,
    overall_max_viewer_count: int = 100,
    session_max_viewer_count: int = 30,
    online: bool = True,
) -> dict[str, Any]:
    """Build a realistic Owncast stream status dict."""
    return {
        "lastConnectTime": last_connect_time,
        "lastDisconnectTime": last_disconnect_time,
        "versionNumber": version_number,
        "streamTitle": stream_title,
        "viewerCount": viewer_count,
        "overallMaxViewerCount": overall_max_viewer_count,
        "sessionMaxViewerCount": session_max_viewer_count,
        "online": online,
    }


class TestParseTimestamp:
    """Exercises _parse_timestamp() with various input formats."""

    @pytest.mark.parametrize(
        ("ts", "expected"),
        [
            pytest.param(None, None, id="none-input"),
            pytest.param("", None, id="empty-string"),
            pytest.param("0001-01-01T00:00:00Z", None, id="go-zero-value"),
            pytest.param(
                "2026-01-15T12:30:00Z",
                datetime(2026, 1, 15, 12, 30, tzinfo=UTC),
                id="utc-z-suffix",
            ),
            pytest.param(
                "2026-01-15T12:30:00+05:30",
                datetime(
                    2026,
                    1,
                    15,
                    12,
                    30,
                    tzinfo=timezone(timedelta(hours=5, minutes=30)),
                ),
                id="offset-timezone",
            ),
            pytest.param(
                "2026-01-15T12:30:00.123456789Z",
                datetime(2026, 1, 15, 12, 30, 0, 123456, tzinfo=UTC),
                id="nanosecond-truncation",
            ),
            pytest.param(
                "2026-01-15T12:30:00.123456Z",
                datetime(2026, 1, 15, 12, 30, 0, 123456, tzinfo=UTC),
                id="microsecond-passthrough",
            ),
            pytest.param(
                "2026-01-15T12:30:00+00:00",
                datetime(2026, 1, 15, 12, 30, tzinfo=UTC),
                id="no-fractional",
            ),
            pytest.param("not-a-date", None, id="malformed"),
        ],
    )
    def test_parse_timestamp(self, ts: str | None, expected: datetime | None) -> None:
        """Timestamp strings are parsed into the expected datetime or None."""
        assert _parse_timestamp(ts) == expected

    def test_malformed_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A malformed timestamp logs a warning via the owlbot.events logger."""
        with caplog.at_level(logging.WARNING, logger="owlbot.events"):
            result = _parse_timestamp("not-a-date")
        assert result is None
        assert len(caplog.records) == 1
        assert "unexpected format" in caplog.records[0].message
        assert "'not-a-date'" in caplog.records[0].message


class TestUser:
    """Exercises User.from_dict() and the is_moderator property."""

    def test_full_payload(self) -> None:
        """All fields are populated from a realistic Owncast user dict."""
        user = User.from_dict(_user_dict())
        assert user.id == "abc123"
        assert user.display_name == "Alice"
        assert user.display_color == 3
        assert user.created_at == datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
        assert user.previous_names == ["OldAlice"]
        assert user.name_changed_at == datetime(2025, 7, 1, 12, 0, tzinfo=UTC)
        assert user.is_bot is False
        assert user.is_authenticated is True
        assert user.scopes == ["MODERATOR"]

    def test_empty_dict(self) -> None:
        """An empty dict produces a User with all defaults."""
        user = User.from_dict({})
        assert user.id == ""
        assert user.display_name == ""
        assert user.display_color == 0
        assert user.created_at is None
        assert user.previous_names == []
        assert user.name_changed_at is None
        assert user.is_bot is False
        assert user.is_authenticated is False
        assert user.scopes == []

    @pytest.mark.parametrize(
        ("scopes", "expected"),
        [
            pytest.param(["MODERATOR"], True, id="has-moderator"),
            pytest.param(["OTHER"], False, id="other-scope"),
            pytest.param([], False, id="empty-scopes"),
            pytest.param(["MOD", "MODERATOR"], True, id="moderator-among-others"),
        ],
    )
    def test_is_moderator(self, scopes: list[str], expected: bool) -> None:
        """is_moderator reflects whether MODERATOR is in scopes."""
        user = User.from_dict({"scopes": scopes})
        assert user.is_moderator is expected

    def test_previous_names(self) -> None:
        """Multiple previous names are preserved in order."""
        user = User.from_dict({"previousNames": ["OldName1", "OldName2"]})
        assert user.previous_names == ["OldName1", "OldName2"]


class TestChatEvent:
    """Exercises ChatEvent.from_dict() with focus on body stripping logic."""

    def test_full_payload(self) -> None:
        """All fields are populated from a realistic CHAT eventData dict."""
        event = ChatEvent.from_dict(_chat_event_dict())
        assert event.user.display_name == "Alice"
        assert event.client_id == 42
        assert event.body == "Hello everyone!"
        assert event.raw_body == "<p>Hello everyone!</p>"
        assert event.message_id == "msg-001"
        assert event.is_visible is True
        assert event.timestamp == datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("<p>hello</p>", "hello", id="strips-p-tags"),
            pytest.param("hello", "hello", id="no-tags"),
            pytest.param("<p>hello", "<p>hello", id="only-open-p"),
            pytest.param("hello</p>", "hello</p>", id="only-close-p"),
            pytest.param("<p><p>inner</p></p>", "<p>inner</p>", id="nested-p"),
            pytest.param("  <p>hello</p>  ", "hello", id="whitespace-around-tags"),
            pytest.param("", "", id="empty-body"),
        ],
    )
    def test_body_stripping(self, raw: str, expected: str) -> None:
        """Paragraph tags are stripped from the message body."""
        event = ChatEvent.from_dict({"body": raw})
        assert event.body == expected

    def test_raw_body_fallback(self) -> None:
        """When rawBody is absent, raw_body falls back to body."""
        event = ChatEvent.from_dict({"body": "<p>hi</p>"})
        assert event.raw_body == "<p>hi</p>"

    def test_empty_dict(self) -> None:
        """An empty dict produces a ChatEvent with all defaults."""
        event = ChatEvent.from_dict({})
        assert event.body == ""
        assert event.raw_body == ""
        assert event.client_id == 0
        assert event.message_id == ""
        assert event.is_visible is True
        assert event.timestamp is None
        assert event.user.id == ""


class TestUserJoinedEvent:
    """Exercises UserJoinedEvent.from_dict()."""

    def test_full_payload(self) -> None:
        """All fields are populated from a realistic USER_JOINED eventData dict."""
        data: dict[str, Any] = {
            "user": _user_dict(user_id="bob456", display_name="Bob"),
            "clientId": 7,
            "id": "evt-001",
            "timestamp": "2026-01-15T12:00:00Z",
        }
        event = UserJoinedEvent.from_dict(data)
        assert event.user.display_name == "Bob"
        assert event.client_id == 7
        assert event.event_id == "evt-001"
        assert event.timestamp == datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    def test_empty_dict(self) -> None:
        """An empty dict produces a UserJoinedEvent with all defaults."""
        event = UserJoinedEvent.from_dict({})
        assert event.user.id == ""
        assert event.client_id == 0
        assert event.event_id == ""
        assert event.timestamp is None


class TestUserPartedEvent:
    """Exercises UserPartedEvent.from_dict()."""

    def test_full_payload(self) -> None:
        """All fields are populated from a realistic USER_PARTED eventData dict."""
        data: dict[str, Any] = {
            "user": _user_dict(user_id="carol789", display_name="Carol"),
            "clientId": 9,
            "id": "evt-010",
            "timestamp": "2026-01-15T13:00:00Z",
        }
        event = UserPartedEvent.from_dict(data)
        assert event.user.display_name == "Carol"
        assert event.client_id == 9
        assert event.event_id == "evt-010"
        assert event.timestamp == datetime(2026, 1, 15, 13, 0, tzinfo=UTC)

    def test_empty_dict(self) -> None:
        """An empty dict produces a UserPartedEvent with all defaults."""
        event = UserPartedEvent.from_dict({})
        assert event.user.id == ""
        assert event.client_id == 0
        assert event.event_id == ""
        assert event.timestamp is None


class TestNameChangedEvent:
    """Exercises NameChangedEvent.from_dict()."""

    def test_full_payload(self) -> None:
        """All fields are populated from a realistic NAME_CHANGE eventData dict."""
        data: dict[str, Any] = {
            "user": _user_dict(display_name="OldName"),
            "clientId": 5,
            "newName": "NewName",
            "id": "evt-002",
            "timestamp": "2026-01-15T14:00:00Z",
        }
        event = NameChangedEvent.from_dict(data)
        assert event.user.display_name == "OldName"
        assert event.new_name == "NewName"
        assert event.client_id == 5
        assert event.event_id == "evt-002"
        assert event.timestamp == datetime(2026, 1, 15, 14, 0, tzinfo=UTC)

    def test_empty_dict(self) -> None:
        """An empty dict produces a NameChangedEvent with all defaults."""
        event = NameChangedEvent.from_dict({})
        assert event.new_name == ""
        assert event.user.id == ""
        assert event.client_id == 0
        assert event.event_id == ""
        assert event.timestamp is None


class TestStreamStartedEvent:
    """Exercises StreamStartedEvent.from_dict()."""

    def test_full_payload(self) -> None:
        """Owncast JSON keys map to the correct Python attributes."""
        event = StreamStartedEvent.from_dict(_stream_event_dict())
        assert event.server_id == "srv-001"
        assert event.server_name == "My Stream"
        assert event.stream_title == "Playing Minecraft"
        assert event.summary == "A fun stream"
        assert event.timestamp == datetime(2026, 1, 15, 18, 0, tzinfo=UTC)

    def test_empty_dict(self) -> None:
        """An empty dict produces a StreamStartedEvent with all defaults."""
        event = StreamStartedEvent.from_dict({})
        assert event.server_id == ""
        assert event.server_name == ""
        assert event.stream_title == ""
        assert event.summary == ""
        assert event.timestamp is None


class TestStreamStoppedEvent:
    """Exercises StreamStoppedEvent.from_dict()."""

    def test_full_payload(self) -> None:
        """Owncast JSON keys map to the correct Python attributes."""
        event = StreamStoppedEvent.from_dict(_stream_event_dict())
        assert event.server_id == "srv-001"
        assert event.server_name == "My Stream"
        assert event.stream_title == "Playing Minecraft"
        assert event.summary == "A fun stream"
        assert event.timestamp == datetime(2026, 1, 15, 18, 0, tzinfo=UTC)

    def test_empty_dict(self) -> None:
        """An empty dict produces a StreamStoppedEvent with all defaults."""
        event = StreamStoppedEvent.from_dict({})
        assert event.server_id == ""
        assert event.server_name == ""
        assert event.stream_title == ""
        assert event.summary == ""
        assert event.timestamp is None


class TestStreamStatus:
    """Exercises StreamStatus.from_dict()."""

    def test_full_payload(self) -> None:
        """All fields are populated from a realistic stream status dict."""
        status = StreamStatus.from_dict(_stream_status_dict())
        assert status.last_connect_time == datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        assert status.last_disconnect_time is None  # Go zero-value in input
        assert status.version_number == "0.2.0"
        assert status.stream_title == "Playing Minecraft"
        assert status.viewer_count == 15
        assert status.overall_max_viewer_count == 100
        assert status.session_max_viewer_count == 30
        assert status.is_online is True

    def test_empty_dict(self) -> None:
        """An empty dict produces a StreamStatus with all defaults."""
        status = StreamStatus.from_dict({})
        assert status.last_connect_time is None
        assert status.last_disconnect_time is None
        assert status.version_number == ""
        assert status.stream_title == ""
        assert status.viewer_count == 0
        assert status.overall_max_viewer_count == 0
        assert status.session_max_viewer_count == 0
        assert status.is_online is False


class TestStreamTitleUpdatedEvent:
    """Exercises StreamTitleUpdatedEvent.from_dict()."""

    def test_with_status(self) -> None:
        """When a status dict is present, the nested StreamStatus is populated."""
        data: dict[str, Any] = {
            **_stream_event_dict(),
            "status": _stream_status_dict(),
        }
        event = StreamTitleUpdatedEvent.from_dict(data)
        assert event.server_id == "srv-001"
        assert event.server_name == "My Stream"
        assert event.stream_title == "Playing Minecraft"
        assert event.summary == "A fun stream"
        assert event.status is not None
        assert event.status.viewer_count == 15
        assert event.status.is_online is True
        assert event.timestamp == datetime(2026, 1, 15, 18, 0, tzinfo=UTC)

    def test_without_status(self) -> None:
        """When the status key is absent, status is None."""
        data = _stream_event_dict()
        event = StreamTitleUpdatedEvent.from_dict(data)
        assert event.status is None

    def test_empty_dict(self) -> None:
        """An empty dict produces a StreamTitleUpdatedEvent with all defaults."""
        event = StreamTitleUpdatedEvent.from_dict({})
        assert event.server_id == ""
        assert event.server_name == ""
        assert event.stream_title == ""
        assert event.summary == ""
        assert event.status is None
        assert event.timestamp is None


class TestVisibilityUpdateEvent:
    """Exercises VisibilityUpdateEvent.from_dict()."""

    def test_full_payload_shown(self) -> None:
        """A visibility-update with visible=True populates all fields."""
        data: dict[str, Any] = {
            "id": "evt-003",
            "ids": ["msg-1", "msg-2"],
            "visible": True,
            "timestamp": "2026-01-15T15:00:00Z",
        }
        event = VisibilityUpdateEvent.from_dict(data)
        assert event.event_id == "evt-003"
        assert event.message_ids == ["msg-1", "msg-2"]
        assert event.is_visible is True
        assert event.timestamp == datetime(2026, 1, 15, 15, 0, tzinfo=UTC)

    def test_full_payload_hidden(self) -> None:
        """A visibility-update with visible=False sets is_visible to False."""
        data: dict[str, Any] = {
            "id": "evt-004",
            "ids": ["msg-3"],
            "visible": False,
            "timestamp": "2026-01-15T15:30:00Z",
        }
        event = VisibilityUpdateEvent.from_dict(data)
        assert event.is_visible is False

    def test_empty_dict(self) -> None:
        """An empty dict produces a VisibilityUpdateEvent with all defaults."""
        event = VisibilityUpdateEvent.from_dict({})
        assert event.event_id == ""
        assert event.message_ids == []
        assert event.is_visible is False
        assert event.timestamp is None


class TestParseEvent:
    """Exercises the top-level parse_event() dispatcher."""

    @pytest.mark.parametrize(
        ("type_str", "expected_type", "expected_class"),
        [
            pytest.param("CHAT", EventType.CHAT, ChatEvent, id="chat"),
            pytest.param(
                "USER_JOINED", EventType.USER_JOINED, UserJoinedEvent, id="user-joined"
            ),
            pytest.param(
                "USER_PARTED", EventType.USER_PARTED, UserPartedEvent, id="user-parted"
            ),
            pytest.param(
                "NAME_CHANGE",
                EventType.NAME_CHANGE,
                NameChangedEvent,
                id="name-change",
            ),
            pytest.param(
                "STREAM_STARTED",
                EventType.STREAM_STARTED,
                StreamStartedEvent,
                id="stream-started",
            ),
            pytest.param(
                "STREAM_STOPPED",
                EventType.STREAM_STOPPED,
                StreamStoppedEvent,
                id="stream-stopped",
            ),
            pytest.param(
                "STREAM_TITLE_UPDATED",
                EventType.STREAM_TITLE_UPDATED,
                StreamTitleUpdatedEvent,
                id="stream-title-updated",
            ),
            pytest.param(
                "VISIBILITY-UPDATE",
                EventType.VISIBILITY_UPDATE,
                VisibilityUpdateEvent,
                id="visibility-update",
            ),
        ],
    )
    def test_parses_event_type(
        self, type_str: str, expected_type: EventType, expected_class: type
    ) -> None:
        """Each event type string produces the correct EventType and event class."""
        result = parse_event({"type": type_str, "eventData": {}})
        assert result is not None
        assert result[0] == expected_type
        assert type(result[1]) is expected_class

    def test_missing_type(self) -> None:
        """A payload with no type key returns None."""
        result = parse_event({})
        assert result is None

    def test_unknown_type(self) -> None:
        """An unrecognized type string returns None."""
        result = parse_event({"type": "FUTURE_EVENT"})
        assert result is None

    def test_missing_event_data(self) -> None:
        """A CHAT payload with no eventData still parses with defaults."""
        result = parse_event({"type": "CHAT"})
        assert result is not None
        assert result[0] == EventType.CHAT
        chat_event = result[1]
        assert isinstance(chat_event, ChatEvent)
        assert chat_event.body == ""


class TestLogEvent:
    """Exercises log_event() output via caplog."""

    @pytest.mark.parametrize(
        ("event_type", "event", "expected_message"),
        [
            pytest.param(
                EventType.CHAT,
                ChatEvent(
                    user=User(
                        id="abc123",
                        display_name="Alice",
                        display_color=3,
                        created_at=None,
                        previous_names=[],
                        name_changed_at=None,
                        is_bot=False,
                        is_authenticated=False,
                        scopes=[],
                    ),
                    client_id=1,
                    body="Hello",
                    raw_body="<p>Hello</p>",
                    message_id="msg-001",
                    is_visible=True,
                    timestamp=None,
                ),
                "[CHAT] Alice (msg-001): Hello",
                id="chat",
            ),
            pytest.param(
                EventType.USER_JOINED,
                UserJoinedEvent(
                    user=User(
                        id="bob456",
                        display_name="Bob",
                        display_color=0,
                        created_at=None,
                        previous_names=[],
                        name_changed_at=None,
                        is_bot=False,
                        is_authenticated=False,
                        scopes=[],
                    ),
                    client_id=1,
                    event_id="evt-001",
                    timestamp=None,
                ),
                "[USER_JOINED] Bob joined.",
                id="user-joined",
            ),
            pytest.param(
                EventType.USER_PARTED,
                UserPartedEvent(
                    user=User(
                        id="carol789",
                        display_name="Carol",
                        display_color=0,
                        created_at=None,
                        previous_names=[],
                        name_changed_at=None,
                        is_bot=False,
                        is_authenticated=False,
                        scopes=[],
                    ),
                    client_id=1,
                    event_id="evt-002",
                    timestamp=None,
                ),
                "[USER_PARTED] Carol parted.",
                id="user-parted",
            ),
            pytest.param(
                EventType.NAME_CHANGE,
                NameChangedEvent(
                    user=User(
                        id="abc123",
                        display_name="OldName",
                        display_color=0,
                        created_at=None,
                        previous_names=[],
                        name_changed_at=None,
                        is_bot=False,
                        is_authenticated=False,
                        scopes=[],
                    ),
                    client_id=1,
                    new_name="NewName",
                    event_id="evt-003",
                    timestamp=None,
                ),
                "[NAME_CHANGE] OldName changed name to NewName.",
                id="name-change",
            ),
            pytest.param(
                EventType.STREAM_STARTED,
                StreamStartedEvent(
                    server_id="srv-001",
                    server_name="My Stream",
                    stream_title="Playing Minecraft",
                    summary="A fun stream",
                    timestamp=None,
                ),
                '[STREAM_STARTED] Stream started: "Playing Minecraft"',
                id="stream-started",
            ),
            pytest.param(
                EventType.STREAM_STOPPED,
                StreamStoppedEvent(
                    server_id="srv-001",
                    server_name="My Stream",
                    stream_title="Playing Minecraft",
                    summary="A fun stream",
                    timestamp=None,
                ),
                "[STREAM_STOPPED] Stream ended.",
                id="stream-stopped",
            ),
            pytest.param(
                EventType.STREAM_TITLE_UPDATED,
                StreamTitleUpdatedEvent(
                    server_id="srv-001",
                    server_name="My Stream",
                    stream_title="New Title",
                    summary="",
                    status=None,
                    timestamp=None,
                ),
                '[STREAM_TITLE_UPDATED] Title changed to "New Title"',
                id="stream-title-updated",
            ),
            pytest.param(
                EventType.VISIBILITY_UPDATE,
                VisibilityUpdateEvent(
                    event_id="evt-004",
                    message_ids=["msg-1", "msg-2"],
                    is_visible=True,
                    timestamp=None,
                ),
                "[VISIBILITY-UPDATE] 2 message(s) shown: msg-1, msg-2",
                id="visibility-shown",
            ),
            pytest.param(
                EventType.VISIBILITY_UPDATE,
                VisibilityUpdateEvent(
                    event_id="evt-005",
                    message_ids=["msg-3"],
                    is_visible=False,
                    timestamp=None,
                ),
                "[VISIBILITY-UPDATE] 1 message(s) hidden: msg-3",
                id="visibility-hidden",
            ),
        ],
    )
    def test_log_message(
        self,
        caplog: pytest.LogCaptureFixture,
        event_type: EventType,
        event: Event,
        expected_message: str,
    ) -> None:
        """log_event produces the correct log message."""
        with caplog.at_level(logging.INFO, logger="owlbot.events"):
            log_event(event_type, event)
        assert caplog.records[0].message == expected_message
