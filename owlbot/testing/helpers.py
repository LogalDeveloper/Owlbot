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

"""Factory functions for building test event and user objects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from owlbot.api.event_types import (
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

if TYPE_CHECKING:
    from datetime import datetime


def make_user(
    *,
    id: str = "test-user-id",  # noqa: A002
    display_name: str = "TestUser",
    display_color: int = 0,
    created_at: datetime | None = None,
    previous_names: list[str] | None = None,
    name_changed_at: datetime | None = None,
    is_bot: bool = False,
    is_authenticated: bool = False,
    is_moderator: bool = False,
    scopes: frozenset[str] | None = None,
) -> User:
    """Build a User with sensible test defaults.

    :param id: User identifier.
    :param display_name: Display name shown in chat.
    :param display_color: Color index for the user's name.
    :param created_at: Account creation timestamp.
    :param previous_names: List of previous display names.
    :param name_changed_at: Timestamp of last name change.
    :param is_bot: Whether the user is a bot.
    :param is_authenticated: Whether the user is authenticated.
    :param is_moderator: When True, adds ``"MODERATOR"`` to scopes.
    :param scopes: Scope set for the user; defaults to empty.
    :return: A populated User instance.
    """
    resolved_scopes = scopes if scopes is not None else frozenset()
    if is_moderator:
        resolved_scopes = resolved_scopes | frozenset({"MODERATOR"})

    return User(
        id=id,
        display_name=display_name,
        display_color=display_color,
        created_at=created_at,
        previous_names=previous_names if previous_names is not None else [],
        name_changed_at=name_changed_at,
        is_bot=is_bot,
        is_authenticated=is_authenticated,
        scopes=resolved_scopes,
    )


def make_chat_event(
    *,
    user: User | None = None,
    raw_body: str = "test message",
    body: str | None = None,
    client_id: int = 0,
    message_id: str = "test-msg-id",
    is_visible: bool = True,
    timestamp: datetime | None = None,
) -> ChatEvent:
    """Build a ChatEvent with sensible test defaults.

    ``raw_body`` is the primary field because it is what the command
    dispatcher parses; tests dispatching commands should set it to the
    command text. ``body`` defaults to a literal copy of ``raw_body``. It
    is **not** Owncast-rendered HTML, so tests that need the rendered form
    must pass ``body=`` explicitly.

    :param user: The chat user; defaults to ``make_user()``.
    :param raw_body: Original user input; also used as the body default.
    :param body: Rendered message body; defaults to *raw_body*.
    :param client_id: Client connection identifier.
    :param message_id: Unique message identifier.
    :param is_visible: Whether the message is visible. Defaults to True;
        chat messages are normally shown.
    :param timestamp: Event timestamp.
    :return: A populated ChatEvent instance.
    """
    return ChatEvent(
        user=user if user is not None else make_user(),
        body=body if body is not None else raw_body,
        raw_body=raw_body,
        client_id=client_id,
        message_id=message_id,
        is_visible=is_visible,
        timestamp=timestamp,
    )


def make_user_joined_event(
    *,
    user: User | None = None,
    client_id: int = 0,
    event_id: str = "test-join-id",
    timestamp: datetime | None = None,
) -> UserJoinedEvent:
    """Build a UserJoinedEvent with sensible test defaults.

    :param user: The joining user; defaults to ``make_user()``.
    :param client_id: Client connection identifier.
    :param event_id: Unique event identifier.
    :param timestamp: Event timestamp.
    :return: A populated UserJoinedEvent instance.
    """
    return UserJoinedEvent(
        user=user if user is not None else make_user(),
        client_id=client_id,
        event_id=event_id,
        timestamp=timestamp,
    )


def make_user_parted_event(
    *,
    user: User | None = None,
    client_id: int = 0,
    event_id: str = "test-part-id",
    timestamp: datetime | None = None,
) -> UserPartedEvent:
    """Build a UserPartedEvent with sensible test defaults.

    :param user: The departing user; defaults to ``make_user()``.
    :param client_id: Client connection identifier.
    :param event_id: Unique event identifier.
    :param timestamp: Event timestamp.
    :return: A populated UserPartedEvent instance.
    """
    return UserPartedEvent(
        user=user if user is not None else make_user(),
        client_id=client_id,
        event_id=event_id,
        timestamp=timestamp,
    )


def make_name_changed_event(
    *,
    user: User | None = None,
    client_id: int = 0,
    new_name: str = "NewName",
    event_id: str = "test-name-change-id",
    timestamp: datetime | None = None,
) -> NameChangedEvent:
    """Build a NameChangedEvent with sensible test defaults.

    :param user: The user changing names; defaults to a user whose
        ``display_name`` matches ``new_name``.
    :param client_id: Client connection identifier.
    :param new_name: The new display name.
    :param event_id: Unique event identifier.
    :param timestamp: Event timestamp.
    :return: A populated NameChangedEvent instance.
    """
    return NameChangedEvent(
        user=user if user is not None else make_user(display_name=new_name),
        client_id=client_id,
        new_name=new_name,
        event_id=event_id,
        timestamp=timestamp,
    )


def make_stream_started_event(
    *,
    server_id: str = "test-server-id",
    server_name: str = "Test Server",
    stream_title: str = "Test Stream",
    summary: str = "",
    timestamp: datetime | None = None,
) -> StreamStartedEvent:
    """Build a StreamStartedEvent with sensible test defaults.

    :param server_id: Server identifier.
    :param server_name: Server display name.
    :param stream_title: Current stream title.
    :param summary: Stream summary text.
    :param timestamp: Event timestamp.
    :return: A populated StreamStartedEvent instance.
    """
    return StreamStartedEvent(
        server_id=server_id,
        server_name=server_name,
        stream_title=stream_title,
        summary=summary,
        timestamp=timestamp,
    )


def make_stream_stopped_event(
    *,
    server_id: str = "test-server-id",
    server_name: str = "Test Server",
    stream_title: str = "Test Stream",
    summary: str = "",
    timestamp: datetime | None = None,
) -> StreamStoppedEvent:
    """Build a StreamStoppedEvent with sensible test defaults.

    :param server_id: Server identifier.
    :param server_name: Server display name.
    :param stream_title: Current stream title.
    :param summary: Stream summary text.
    :param timestamp: Event timestamp.
    :return: A populated StreamStoppedEvent instance.
    """
    return StreamStoppedEvent(
        server_id=server_id,
        server_name=server_name,
        stream_title=stream_title,
        summary=summary,
        timestamp=timestamp,
    )


def make_stream_status(
    *,
    last_connect_time: datetime | None = None,
    last_disconnect_time: datetime | None = None,
    version_number: str = "",
    stream_title: str = "Test Stream",
    viewer_count: int = 0,
    overall_max_viewer_count: int = 0,
    session_max_viewer_count: int = 0,
    is_online: bool = False,
) -> StreamStatus:
    """Build a StreamStatus with sensible test defaults.

    :param last_connect_time: Timestamp of the last stream connect.
    :param last_disconnect_time: Timestamp of the last stream disconnect.
    :param version_number: Owncast server version string.
    :param stream_title: Current stream title.
    :param viewer_count: Current viewer count.
    :param overall_max_viewer_count: All-time peak viewer count.
    :param session_max_viewer_count: Current-session peak viewer count.
    :param is_online: Whether the stream is currently live.
    :return: A populated StreamStatus instance.
    """
    return StreamStatus(
        last_connect_time=last_connect_time,
        last_disconnect_time=last_disconnect_time,
        version_number=version_number,
        stream_title=stream_title,
        viewer_count=viewer_count,
        overall_max_viewer_count=overall_max_viewer_count,
        session_max_viewer_count=session_max_viewer_count,
        is_online=is_online,
    )


def make_stream_title_updated_event(
    *,
    server_id: str = "test-server-id",
    server_name: str = "Test Server",
    stream_title: str = "Test Stream",
    summary: str = "",
    status: StreamStatus | None = None,
    timestamp: datetime | None = None,
) -> StreamTitleUpdatedEvent:
    """Build a StreamTitleUpdatedEvent with sensible test defaults.

    :param server_id: Server identifier.
    :param server_name: Server display name.
    :param stream_title: Current stream title.
    :param summary: Stream summary text.
    :param status: Optional stream status snapshot.
    :param timestamp: Event timestamp.
    :return: A populated StreamTitleUpdatedEvent instance.
    """
    return StreamTitleUpdatedEvent(
        server_id=server_id,
        server_name=server_name,
        stream_title=stream_title,
        summary=summary,
        status=status,
        timestamp=timestamp,
    )


def make_visibility_update_event(
    *,
    event_id: str = "test-vis-id",
    message_ids: list[str] | None = None,
    is_visible: bool = False,
    timestamp: datetime | None = None,
) -> VisibilityUpdateEvent:
    """Build a VisibilityUpdateEvent with sensible test defaults.

    :param event_id: Unique event identifier.
    :param message_ids: List of affected message IDs.
    :param is_visible: Whether the messages are now visible. Defaults to
        False; visibility updates are primarily used to hide messages.
    :param timestamp: Event timestamp.
    :return: A populated VisibilityUpdateEvent instance.
    """
    return VisibilityUpdateEvent(
        event_id=event_id,
        message_ids=message_ids if message_ids is not None else [],
        is_visible=is_visible,
        timestamp=timestamp,
    )
