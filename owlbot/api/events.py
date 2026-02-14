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

"""Event handler registration decorators and types.

This module provides the module-facing API for event handlers:
- @on_event decorator for registering handlers
- Priority class for handler ordering
- EventHandler type alias
"""

from enum import IntEnum
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .context import EventContext
    from .event_types import EventType


class EventMark(TypedDict):
    """Type for the event marker attribute set by @on_event."""

    event_types: tuple[EventType, ...]
    priority: int


type EventHandler = "Callable[[EventContext[Any]], Awaitable[None]]"


class Priority(IntEnum):
    """
    Standard priority levels for event handlers.

    Higher values run first. Handlers at the same priority level run in
    registration order. Custom numeric values can be used for fine-grained control.
    """

    HIGHEST = 100  # Filters, rate limiting, authentication.
    HIGH = 75  # Moderation, logging.
    NORMAL = 50  # Default for most handlers.
    LOW = 25  # Reactions, notifications.
    LOWEST = 0  # Stats collection, cleanup.


def on_event(
    *event_types: EventType,
    priority: int = Priority.NORMAL,
) -> Callable[[EventHandler], EventHandler]:
    """
    Decorator to register a function as a handler for one or more event types.

    The decorated function will be called whenever an event of the specified
    type(s) is received. Handlers are executed sequentially in priority order
    (highest priority first). Handlers at the same priority run in registration order.

    :param event_types: One or more EventType enum values.
    :param priority: Handler priority (higher runs first).
        Default: Priority.NORMAL (50).
    :return: Decorator that marks the function for registration by the module loader.
    """

    def decorator(func: EventHandler) -> EventHandler:
        # Mark the function with event info for deferred registration.
        # The module loader will scan for this attribute and register handlers.
        func._owlbot_event = EventMark(  # type: ignore[attr-defined]
            event_types=event_types, priority=priority
        )
        return func

    return decorator
