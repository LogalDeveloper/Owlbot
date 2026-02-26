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

"""Chat message counting for the timers module.

Tracks chat messages per timer to support the minimum chat lines threshold.
Bot messages and hidden messages are excluded from the count.
"""

from owlbot.api import (
    ChatEvent,
    EventContext,
    EventType,
    Priority,
    VisibilityUpdateEvent,
    on_event,
)

from .scheduler import get_scheduler


@on_event(EventType.CHAT, priority=Priority.LOWEST)
async def count_chat_message(ctx: EventContext[ChatEvent]) -> None:
    """Count a chat message for all tracked timers.

    Runs at lowest priority so all other CHAT handlers (moderation, etc.)
    execute first. Skips bot messages and hidden messages.

    :param ctx: The event context with the chat event.
    """
    event = ctx.event

    if event.user.is_bot or not event.is_visible:
        reason = "bot message" if event.user.is_bot else "hidden message"
        ctx.logger.debug("Skipping chat count for %s: %s.", event.message_id, reason)
        return

    counted_ids = get_scheduler(ctx.module).counted_ids
    ctx.logger.debug(
        "Counting message %s from %s for %d timer(s).",
        event.message_id,
        event.user.display_name,
        len(counted_ids),
    )
    for timer_set in counted_ids.values():
        timer_set.add(event.message_id)


@on_event(EventType.VISIBILITY_UPDATE, priority=Priority.LOWEST)
async def handle_visibility_update(ctx: EventContext[VisibilityUpdateEvent]) -> None:
    """Remove hidden messages from chat counts.

    Only handles the hide case. Un-hiding does not re-add messages because
    we cannot distinguish previously counted user messages from bot messages
    that were never counted.

    :param ctx: The event context with the visibility update event.
    """
    event = ctx.event

    if event.is_visible:
        return

    affected = set(event.message_ids)
    counted_ids = get_scheduler(ctx.module).counted_ids

    ctx.logger.debug(
        "Removing %d hidden message(s) from chat counts.",
        len(affected),
    )
    for timer_set in counted_ids.values():
        timer_set -= affected
