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

"""Event dataclasses for Owncast webhook payloads."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

_NANO_RE = re.compile(r"(\.\d{6})\d+")

logger = logging.getLogger("owlbot.events")


class EventType(StrEnum):
    """Supported event types for Owncast webhooks."""

    CHAT = "CHAT"
    USER_JOINED = "USER_JOINED"
    USER_PARTED = "USER_PARTED"
    NAME_CHANGE = "NAME_CHANGE"
    STREAM_STARTED = "STREAM_STARTED"
    STREAM_STOPPED = "STREAM_STOPPED"
    STREAM_TITLE_UPDATED = "STREAM_TITLE_UPDATED"
    # Owncast sends this type with a dash rather than an underscore.
    # The enum member uses an underscore so modules see a consistent naming
    # style; the string value preserves the original for webhook matching.
    VISIBILITY_UPDATE = "VISIBILITY-UPDATE"


def _parse_timestamp(ts: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp string into a datetime.

    Handles timezone suffixes and the Go zero-value timestamp that Owncast
    sends for missing/unset timestamps.

    :param ts: The timestamp string to parse.
    :return: Parsed datetime, or None if the timestamp is empty or invalid.
    """
    # Owncast (written in Go) sends "0001-01-01T00:00:00Z" for unset time.Time values.
    if not ts or ts == "0001-01-01T00:00:00Z":
        return None

    # Replace "Z" (UTC indicator) with the equivalent offset that
    # fromisoformat understands.  Timezone offsets like
    # "+05:30" are kept as-is so the returned datetime is always aware.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"

    # Go's time.Time emits nanosecond-precision fractional seconds (9 digits),
    # but Python's datetime only supports microseconds (6 digits).  Truncate
    # any excess fractional digits to avoid a ValueError from fromisoformat().
    ts = _NANO_RE.sub(r"\1", ts)

    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        # Malformed timestamp. Return None rather than crashing.
        logger.warning(f"Owncast API returned timestamp in unexpected format: {ts!r}")
        return None


@dataclass(frozen=True, slots=True)
class User:
    """Represents a chat user's identity and metadata."""

    id: str
    display_name: str
    display_color: int
    created_at: datetime | None
    previous_names: list[str]
    name_changed_at: datetime | None
    is_bot: bool
    is_authenticated: bool
    scopes: frozenset[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> User:
        """Create a User from webhook JSON data.

        :param data: The user data from the webhook payload.
        :return: A populated User instance.
        """
        return cls(
            id=data.get("id", ""),
            display_name=data.get("displayName", ""),
            display_color=data.get("displayColor", 0),
            created_at=_parse_timestamp(data.get("createdAt")),
            previous_names=data.get("previousNames", []),
            name_changed_at=_parse_timestamp(data.get("nameChangedAt")),
            is_bot=data.get("isBot", False),
            is_authenticated=data.get("authenticated", False),
            scopes=frozenset(data.get("scopes", ())),
        )

    @property
    def is_moderator(self) -> bool:
        """Check if the user has moderator privileges.

        :return: True if the user has the MODERATOR scope.
        """
        return "MODERATOR" in self.scopes


@dataclass(frozen=True, slots=True)
class ChatEvent:
    """Represents a chat message sent by a user."""

    user: User
    client_id: int

    # Rendered HTML from Owncast (markdown converted, custom emotes as <img>
    # tags, outer <p> wrapper stripped).
    body: str

    # Original user input before Owncast rendering.
    raw_body: str

    message_id: str
    is_visible: bool
    timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatEvent:
        """Create a ChatEvent from webhook JSON data.

        :param data: The event data from the webhook payload.
        :return: A populated ChatEvent instance.
        """
        # Owncast wraps the rendered body in <p> tags. Strip the outer
        # wrapper so handlers don't have to deal with it, but the
        # contents are still rendered HTML (bold, links, emote <img>s, etc.).
        body = data.get("body", "").strip()

        if body.startswith("<p>") and body.endswith("</p>"):
            body = body[3:-4]

        return cls(
            user=User.from_dict(data.get("user", {})),
            client_id=data.get("clientId", 0),
            body=body,
            raw_body=data.get("rawBody", data.get("body", "")),
            message_id=data.get("id", ""),
            is_visible=data.get("visible", True),
            timestamp=_parse_timestamp(data.get("timestamp")),
        )


@dataclass(frozen=True, slots=True)
class UserJoinedEvent:
    """Fired when a user joins the chat."""

    user: User
    client_id: int
    event_id: str
    timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserJoinedEvent:
        """Create a UserJoinedEvent from webhook JSON data.

        :param data: The event data from the webhook payload.
        :return: A populated UserJoinedEvent instance.
        """
        return cls(
            user=User.from_dict(data.get("user", {})),
            client_id=data.get("clientId", 0),
            event_id=data.get("id", ""),
            timestamp=_parse_timestamp(data.get("timestamp")),
        )


@dataclass(frozen=True, slots=True)
class UserPartedEvent:
    """Fired when a user disconnects from chat."""

    user: User
    client_id: int
    event_id: str
    timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserPartedEvent:
        """Create a UserPartedEvent from webhook JSON data.

        :param data: The event data from the webhook payload.
        :return: A populated UserPartedEvent instance.
        """
        return cls(
            user=User.from_dict(data.get("user", {})),
            client_id=data.get("clientId", 0),
            event_id=data.get("id", ""),
            timestamp=_parse_timestamp(data.get("timestamp")),
        )


@dataclass(frozen=True, slots=True)
class NameChangedEvent:
    """Fired when a user changes their display name."""

    user: User
    client_id: int
    new_name: str
    event_id: str
    timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NameChangedEvent:
        """Create a NameChangedEvent from webhook JSON data.

        Note: The user object contains the OLD display name in user.display_name.

        :param data: The event data from the webhook payload.
        :return: A populated NameChangedEvent instance.
        """
        return cls(
            user=User.from_dict(data.get("user", {})),
            client_id=data.get("clientId", 0),
            new_name=data.get("newName", ""),
            event_id=data.get("id", ""),
            timestamp=_parse_timestamp(data.get("timestamp")),
        )


@dataclass(frozen=True, slots=True)
class StreamStartedEvent:
    """Fired when the stream goes live."""

    server_id: str
    server_name: str
    stream_title: str
    summary: str
    timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamStartedEvent:
        """Create a StreamStartedEvent from webhook JSON data.

        :param data: The event data from the webhook payload.
        :return: A populated StreamStartedEvent instance.
        """
        return cls(
            server_id=data.get("id", ""),
            server_name=data.get("name", ""),
            stream_title=data.get("streamTitle", ""),
            summary=data.get("summary", ""),
            timestamp=_parse_timestamp(data.get("timestamp")),
        )


@dataclass(frozen=True, slots=True)
class StreamStoppedEvent:
    """Fired when the stream goes offline."""

    server_id: str
    server_name: str
    stream_title: str
    summary: str
    timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamStoppedEvent:
        """Create a StreamStoppedEvent from webhook JSON data.

        :param data: The event data from the webhook payload.
        :return: A populated StreamStoppedEvent instance.
        """
        return cls(
            server_id=data.get("id", ""),
            server_name=data.get("name", ""),
            stream_title=data.get("streamTitle", ""),
            summary=data.get("summary", ""),
            timestamp=_parse_timestamp(data.get("timestamp")),
        )


@dataclass(frozen=True, slots=True)
class StreamStatus:
    """Current stream status snapshot included in some events."""

    last_connect_time: datetime | None
    last_disconnect_time: datetime | None
    version_number: str
    stream_title: str
    viewer_count: int
    overall_max_viewer_count: int
    session_max_viewer_count: int
    is_online: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamStatus:
        """Create a StreamStatus from webhook JSON data.

        :param data: The status data from the webhook payload.
        :return: A populated StreamStatus instance.
        """
        return cls(
            last_connect_time=_parse_timestamp(data.get("lastConnectTime")),
            last_disconnect_time=_parse_timestamp(data.get("lastDisconnectTime")),
            version_number=data.get("versionNumber", ""),
            stream_title=data.get("streamTitle", ""),
            viewer_count=data.get("viewerCount", 0),
            overall_max_viewer_count=data.get("overallMaxViewerCount", 0),
            session_max_viewer_count=data.get("sessionMaxViewerCount", 0),
            is_online=data.get("online", False),
        )


@dataclass(frozen=True, slots=True)
class StreamTitleUpdatedEvent:
    """Fired when the stream title is changed."""

    server_id: str
    server_name: str
    stream_title: str
    summary: str
    status: StreamStatus | None
    timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamTitleUpdatedEvent:
        """Create a StreamTitleUpdatedEvent from webhook JSON data.

        :param data: The event data from the webhook payload.
        :return: A populated StreamTitleUpdatedEvent instance.
        """
        status_data = data.get("status")
        return cls(
            server_id=data.get("id", ""),
            server_name=data.get("name", ""),
            stream_title=data.get("streamTitle", ""),
            summary=data.get("summary", ""),
            status=StreamStatus.from_dict(status_data) if status_data else None,
            timestamp=_parse_timestamp(data.get("timestamp")),
        )


@dataclass(frozen=True, slots=True)
class VisibilityUpdateEvent:
    """Fired when message visibility changes (moderation action)."""

    event_id: str
    message_ids: list[str]
    is_visible: bool
    timestamp: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisibilityUpdateEvent:
        """Create a VisibilityUpdateEvent from webhook JSON data.

        :param data: The event data from the webhook payload.
        :return: A populated VisibilityUpdateEvent instance.
        """
        return cls(
            event_id=data.get("id", ""),
            message_ids=data.get("ids", []),
            is_visible=data.get("visible", False),
            timestamp=_parse_timestamp(data.get("timestamp")),
        )


# Union type for type hints accepting any event.
type Event = (
    ChatEvent
    | UserJoinedEvent
    | UserPartedEvent
    | NameChangedEvent
    | StreamStartedEvent
    | StreamStoppedEvent
    | StreamTitleUpdatedEvent
    | VisibilityUpdateEvent
)

# Tuple of (event_type, event_instance) returned by parse_event().
type _ParsedEvent = tuple[EventType, Event]

# Maps EventType enum values to their corresponding event classes.
_EVENT_TYPE_MAP: dict[EventType, type[Event]] = {
    EventType.CHAT: ChatEvent,
    EventType.USER_JOINED: UserJoinedEvent,
    EventType.USER_PARTED: UserPartedEvent,
    EventType.NAME_CHANGE: NameChangedEvent,
    EventType.STREAM_STARTED: StreamStartedEvent,
    EventType.STREAM_STOPPED: StreamStoppedEvent,
    EventType.STREAM_TITLE_UPDATED: StreamTitleUpdatedEvent,
    EventType.VISIBILITY_UPDATE: VisibilityUpdateEvent,
}


def parse_event(data: dict[str, Any]) -> _ParsedEvent | None:
    """Parse a raw webhook payload into a typed event.

    :param data: The JSON payload from Owncast's webhook.
    :return: Tuple of (event_type, event_instance), or None if the event type
        is not recognized.
    """
    event_type_str: str | None = data.get("type")
    event_data = data.get("eventData", {})

    if event_type_str is None:
        return None

    try:
        event_type = EventType(event_type_str)
    except ValueError:
        # Unknown event type. This could be a new Owncast feature we don't support yet.
        return None

    event_class = _EVENT_TYPE_MAP[event_type]
    return event_type, event_class.from_dict(event_data)


def log_event(event_type: EventType, event: Event) -> None:
    """Log event details for debugging and monitoring.

    :param event_type: The type of event being logged.
    :param event: The parsed event instance.
    """
    match event:
        case ChatEvent(user=user, message_id=mid, body=body):
            logger.info(f"[{event_type}] {user.display_name} ({mid}): {body}")
        case UserJoinedEvent(user=user):
            logger.info(f"[{event_type}] {user.display_name} joined.")
        case UserPartedEvent(user=user):
            logger.info(f"[{event_type}] {user.display_name} parted.")
        case NameChangedEvent(user=user, new_name=new_name):
            logger.info(
                f"[{event_type}] {user.display_name} changed name to {new_name}."
            )
        case StreamStartedEvent(stream_title=title):
            logger.info(f'[{event_type}] Stream started: "{title}"')
        case StreamStoppedEvent():
            logger.info(f"[{event_type}] Stream ended.")
        case StreamTitleUpdatedEvent(stream_title=title):
            logger.info(f'[{event_type}] Title changed to "{title}"')
        case VisibilityUpdateEvent(is_visible=visible, message_ids=ids):
            action = "shown" if visible else "hidden"
            id_list = ", ".join(ids)
            logger.info(f"[{event_type}] {len(ids)} message(s) {action}: {id_list}")
