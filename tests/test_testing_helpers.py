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

"""Tests for owlbot.testing helper factory functions."""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from owlbot import testing as testing_pkg
from owlbot.api.event_types import (
    _EVENT_TYPE_MAP,
    ChatEvent,
    NameChangedEvent,
    StreamStartedEvent,
    StreamStatus,
    StreamStoppedEvent,
    StreamTitleUpdatedEvent,
    User,
    UserJoinedEvent,
    UserPartedEvent,
    VisibilityUpdateEvent,
)
from owlbot.testing import (
    make_chat_event,
    make_name_changed_event,
    make_stream_started_event,
    make_stream_status,
    make_stream_stopped_event,
    make_stream_title_updated_event,
    make_user,
    make_user_joined_event,
    make_user_parted_event,
    make_visibility_update_event,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class TestMakeUser:
    """Exercises the make_user() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid User with expected fields."""
        user = make_user()
        assert isinstance(user, User)
        assert user.id == "test-user-id"
        assert user.display_name == "TestUser"
        assert user.display_color == 0
        assert user.created_at is None
        assert user.previous_names == []
        assert user.name_changed_at is None
        assert user.is_bot is False
        assert user.is_authenticated is False
        assert user.scopes == frozenset()
        assert user.is_moderator is False

    def test_is_authenticated_flag(self) -> None:
        """is_authenticated=True sets is_authenticated on the User."""
        user = make_user(is_authenticated=True)
        assert user.is_authenticated is True

    def test_is_moderator_shorthand(self) -> None:
        """is_moderator=True adds MODERATOR to scopes."""
        user = make_user(is_moderator=True)
        assert user.is_moderator is True
        assert "MODERATOR" in user.scopes

    def test_is_moderator_with_explicit_scopes(self) -> None:
        """is_moderator=True unions with explicitly provided scopes."""
        user = make_user(is_moderator=True, scopes=frozenset({"CUSTOM"}))
        assert user.scopes == frozenset({"MODERATOR", "CUSTOM"})
        assert user.is_moderator is True

    def test_explicit_scopes_without_is_moderator(self) -> None:
        """Explicit scopes without is_moderator=True are used as-is."""
        user = make_user(scopes=frozenset({"CUSTOM"}))
        assert user.scopes == frozenset({"CUSTOM"})
        assert user.is_moderator is False

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        user = make_user(
            id="custom-id",
            display_name="CustomUser",
            display_color=5,
            is_bot=True,
            previous_names=["OldName"],
        )
        assert user.id == "custom-id"
        assert user.display_name == "CustomUser"
        assert user.display_color == 5
        assert user.is_bot is True
        assert user.previous_names == ["OldName"]


class TestMakeChatEvent:
    """Exercises the make_chat_event() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid ChatEvent."""
        event = make_chat_event()
        assert isinstance(event, ChatEvent)
        assert isinstance(event.user, User)
        assert event.body == "test message"
        assert event.raw_body == "test message"
        assert event.client_id == 0
        assert event.message_id == "test-msg-id"
        assert event.is_visible is True
        assert event.timestamp is None

    def test_body_defaults_to_raw_body(self) -> None:
        """When body is unspecified, it mirrors the raw_body value."""
        event = make_chat_event(raw_body="custom body")
        assert event.raw_body == "custom body"
        assert event.body == "custom body"

    def test_body_explicit(self) -> None:
        """An explicit body overrides the raw_body mirror."""
        event = make_chat_event(raw_body="rendered", body="<p>rendered</p>")
        assert event.raw_body == "rendered"
        assert event.body == "<p>rendered</p>"

    def test_custom_user(self) -> None:
        """A custom User object is used instead of the default."""
        custom_user = make_user(display_name="Alice")
        event = make_chat_event(user=custom_user)
        assert event.user.display_name == "Alice"

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        event = make_chat_event(
            raw_body="hello",
            client_id=42,
            message_id="msg-42",
            is_visible=False,
            timestamp=ts,
        )
        assert event.raw_body == "hello"
        assert event.body == "hello"
        assert event.client_id == 42
        assert event.message_id == "msg-42"
        assert event.is_visible is False
        assert event.timestamp == ts


class TestMakeUserJoinedEvent:
    """Exercises the make_user_joined_event() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid UserJoinedEvent."""
        event = make_user_joined_event()
        assert isinstance(event, UserJoinedEvent)
        assert isinstance(event.user, User)
        assert event.client_id == 0
        assert event.event_id == "test-join-id"
        assert event.timestamp is None

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        custom_user = make_user(display_name="Bob")
        ts = datetime(2026, 3, 1, tzinfo=UTC)
        event = make_user_joined_event(
            user=custom_user,
            client_id=7,
            event_id="custom-join",
            timestamp=ts,
        )
        assert event.user.display_name == "Bob"
        assert event.client_id == 7
        assert event.event_id == "custom-join"
        assert event.timestamp == ts


class TestMakeUserPartedEvent:
    """Exercises the make_user_parted_event() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid UserPartedEvent."""
        event = make_user_parted_event()
        assert isinstance(event, UserPartedEvent)
        assert isinstance(event.user, User)
        assert event.client_id == 0
        assert event.event_id == "test-part-id"
        assert event.timestamp is None

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        custom_user = make_user(display_name="Carol")
        ts = datetime(2026, 4, 1, tzinfo=UTC)
        event = make_user_parted_event(
            user=custom_user,
            client_id=9,
            event_id="custom-part",
            timestamp=ts,
        )
        assert event.user.display_name == "Carol"
        assert event.client_id == 9
        assert event.event_id == "custom-part"
        assert event.timestamp == ts


class TestMakeNameChangedEvent:
    """Exercises the make_name_changed_event() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid NameChangedEvent."""
        event = make_name_changed_event()
        assert isinstance(event, NameChangedEvent)
        assert isinstance(event.user, User)
        assert event.client_id == 0
        assert event.new_name == "NewName"
        assert event.event_id == "test-name-change-id"
        assert event.timestamp is None

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        custom_user = make_user(display_name="OldName")
        ts = datetime(2026, 5, 1, tzinfo=UTC)
        event = make_name_changed_event(
            user=custom_user,
            client_id=5,
            new_name="BrandNew",
            event_id="custom-name-change",
            timestamp=ts,
        )
        assert event.user.display_name == "OldName"
        assert event.new_name == "BrandNew"
        assert event.client_id == 5
        assert event.event_id == "custom-name-change"
        assert event.timestamp == ts


class TestMakeStreamStartedEvent:
    """Exercises the make_stream_started_event() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid StreamStartedEvent."""
        event = make_stream_started_event()
        assert isinstance(event, StreamStartedEvent)
        assert event.server_id == "test-server-id"
        assert event.server_name == "Test Server"
        assert event.stream_title == "Test Stream"
        assert event.summary == ""
        assert event.timestamp is None

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        ts = datetime(2026, 6, 1, tzinfo=UTC)
        event = make_stream_started_event(
            server_id="srv-42",
            server_name="My Server",
            stream_title="Playing Games",
            summary="A fun stream",
            timestamp=ts,
        )
        assert event.server_id == "srv-42"
        assert event.server_name == "My Server"
        assert event.stream_title == "Playing Games"
        assert event.summary == "A fun stream"
        assert event.timestamp == ts


class TestMakeStreamStoppedEvent:
    """Exercises the make_stream_stopped_event() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid StreamStoppedEvent."""
        event = make_stream_stopped_event()
        assert isinstance(event, StreamStoppedEvent)
        assert event.server_id == "test-server-id"
        assert event.server_name == "Test Server"
        assert event.stream_title == "Test Stream"
        assert event.summary == ""
        assert event.timestamp is None

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        ts = datetime(2026, 7, 1, tzinfo=UTC)
        event = make_stream_stopped_event(
            server_id="srv-99",
            server_name="Other Server",
            stream_title="Done Streaming",
            summary="It was great",
            timestamp=ts,
        )
        assert event.server_id == "srv-99"
        assert event.server_name == "Other Server"
        assert event.stream_title == "Done Streaming"
        assert event.summary == "It was great"
        assert event.timestamp == ts


class TestMakeStreamStatus:
    """Exercises the make_stream_status() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid StreamStatus."""
        status = make_stream_status()
        assert isinstance(status, StreamStatus)
        assert status.last_connect_time is None
        assert status.last_disconnect_time is None
        assert status.version_number == ""
        assert status.stream_title == "Test Stream"
        assert status.viewer_count == 0
        assert status.overall_max_viewer_count == 0
        assert status.session_max_viewer_count == 0
        assert status.is_online is False

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        connect_ts = datetime(2026, 1, 1, tzinfo=UTC)
        disconnect_ts = datetime(2026, 1, 2, tzinfo=UTC)
        status = make_stream_status(
            last_connect_time=connect_ts,
            last_disconnect_time=disconnect_ts,
            version_number="0.2.0",
            stream_title="Playing Games",
            viewer_count=42,
            overall_max_viewer_count=100,
            session_max_viewer_count=75,
            is_online=True,
        )
        assert status.last_connect_time == connect_ts
        assert status.last_disconnect_time == disconnect_ts
        assert status.version_number == "0.2.0"
        assert status.stream_title == "Playing Games"
        assert status.viewer_count == 42
        assert status.overall_max_viewer_count == 100
        assert status.session_max_viewer_count == 75
        assert status.is_online is True


class TestMakeStreamTitleUpdatedEvent:
    """Exercises the make_stream_title_updated_event() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid StreamTitleUpdatedEvent."""
        event = make_stream_title_updated_event()
        assert isinstance(event, StreamTitleUpdatedEvent)
        assert event.server_id == "test-server-id"
        assert event.server_name == "Test Server"
        assert event.stream_title == "Test Stream"
        assert event.summary == ""
        assert event.status is None
        assert event.timestamp is None

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        ts = datetime(2026, 6, 15, tzinfo=UTC)
        status = StreamStatus(
            last_connect_time=ts,
            last_disconnect_time=None,
            version_number="0.2.0",
            stream_title="Playing Games",
            viewer_count=10,
            overall_max_viewer_count=50,
            session_max_viewer_count=20,
            is_online=True,
        )
        event = make_stream_title_updated_event(
            server_id="srv-42",
            server_name="My Server",
            stream_title="Playing Games",
            summary="A fun stream",
            status=status,
            timestamp=ts,
        )
        assert event.server_id == "srv-42"
        assert event.server_name == "My Server"
        assert event.stream_title == "Playing Games"
        assert event.summary == "A fun stream"
        assert event.status is status
        assert event.timestamp == ts


class TestMakeVisibilityUpdateEvent:
    """Exercises the make_visibility_update_event() factory function."""

    def test_defaults(self) -> None:
        """All default values produce a valid VisibilityUpdateEvent."""
        event = make_visibility_update_event()
        assert isinstance(event, VisibilityUpdateEvent)
        assert event.event_id == "test-vis-id"
        assert event.message_ids == []
        assert event.is_visible is False
        assert event.timestamp is None

    def test_custom_overrides(self) -> None:
        """All parameters can be overridden."""
        ts = datetime(2026, 8, 1, tzinfo=UTC)
        event = make_visibility_update_event(
            event_id="vis-42",
            message_ids=["msg-1", "msg-2"],
            is_visible=True,
            timestamp=ts,
        )
        assert event.event_id == "vis-42"
        assert event.message_ids == ["msg-1", "msg-2"]
        assert event.is_visible is True
        assert event.timestamp == ts


class TestDefaultsMatchFromDict:
    """Non-identifier factory defaults match each dataclass's from_dict({})."""

    @pytest.mark.parametrize(
        ("factory", "dataclass_cls", "identifier_fields"),
        [
            pytest.param(make_user, User, {"id", "display_name"}, id="user"),
            pytest.param(
                make_chat_event,
                ChatEvent,
                {"body", "raw_body", "message_id", "user"},
                id="chat-event",
            ),
            pytest.param(
                make_user_joined_event,
                UserJoinedEvent,
                {"event_id", "user"},
                id="user-joined-event",
            ),
            pytest.param(
                make_user_parted_event,
                UserPartedEvent,
                {"event_id", "user"},
                id="user-parted-event",
            ),
            pytest.param(
                make_name_changed_event,
                NameChangedEvent,
                {"event_id", "new_name", "user"},
                id="name-changed-event",
            ),
            pytest.param(
                make_stream_started_event,
                StreamStartedEvent,
                {"server_id", "server_name", "stream_title"},
                id="stream-started-event",
            ),
            pytest.param(
                make_stream_stopped_event,
                StreamStoppedEvent,
                {"server_id", "server_name", "stream_title"},
                id="stream-stopped-event",
            ),
            pytest.param(
                make_stream_status,
                StreamStatus,
                {"stream_title"},
                id="stream-status",
            ),
            pytest.param(
                make_stream_title_updated_event,
                StreamTitleUpdatedEvent,
                {"server_id", "server_name", "stream_title"},
                id="stream-title-updated-event",
            ),
            pytest.param(
                make_visibility_update_event,
                VisibilityUpdateEvent,
                {"event_id"},
                id="visibility-update-event",
            ),
        ],
    )
    def test_defaults_match(
        self,
        factory: Callable[..., Any],
        dataclass_cls: Any,
        identifier_fields: set[str],
    ) -> None:
        """Every non-identifier default must equal dataclass_cls.from_dict({})."""
        from_factory = factory()
        from_empty_dict = dataclass_cls.from_dict({})
        for field in dataclasses.fields(dataclass_cls):
            if field.name in identifier_fields:
                continue
            assert getattr(from_factory, field.name) == getattr(
                from_empty_dict, field.name
            ), f"{dataclass_cls.__name__}.{field.name}"


class TestEventTypeFactoryCoverage:
    """Guards against EventType additions without a matching owlbot.testing factory."""

    def test_every_event_type_has_a_factory(self) -> None:
        """Every EventType must map to a ``make_<snake_case_dataclass>`` factory.

        Derives the expected factory name from the dataclass name
        (``StreamStartedEvent`` -> ``make_stream_started_event``) so no
        separate lookup table has to stay in sync.
        """
        missing: list[str] = []
        for event_type, dataclass_cls in _EVENT_TYPE_MAP.items():
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", dataclass_cls.__name__).lower()
            factory_name = f"make_{snake}"
            if not hasattr(testing_pkg, factory_name):
                missing.append(
                    f"{event_type.name} -> {dataclass_cls.__name__}: "
                    f"no {factory_name!r} in owlbot.testing"
                )
        assert not missing, "Missing factories:\n  " + "\n  ".join(missing)
