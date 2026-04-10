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

"""Event handlers for the timers module."""

from owlbot.api import (
    ChatEvent,
    EventContext,
    EventType,
    Priority,
    StreamStartedEvent,
    StreamStoppedEvent,
    VisibilityUpdateEvent,
    on_event,
)

from .manager import get_manager


@on_event(EventType.STREAM_STARTED)
async def handle_stream_started(ctx: EventContext[StreamStartedEvent]) -> None:
    """Start timers when the stream goes live.

    Cancels any pending offline stop (stream returned within grace period)
    and starts all enabled timers.

    :param ctx: The event context.
    """
    manager = get_manager(ctx.module)
    manager.cancel_offline_stop()
    await manager.start_all()
    ctx.logger.info("Stream started. Timers activated.")


@on_event(EventType.STREAM_STOPPED)
async def handle_stream_stopped(ctx: EventContext[StreamStoppedEvent]) -> None:
    """Schedule timers to stop after a grace period.

    If the stream returns within the grace period, the stop is cancelled
    by handle_stream_started.

    :param ctx: The event context.
    """
    manager = get_manager(ctx.module)
    manager.schedule_offline_stop()
    ctx.logger.info(
        "Stream stopped. Timers will stop in %.0f seconds.",
        manager.OFFLINE_STOP_DELAY,
    )


@on_event(EventType.CHAT, priority=Priority.LOWEST)
async def count_chat_message(ctx: EventContext[ChatEvent]) -> None:
    """Count a chat message for all active timers.

    Runs at lowest priority so all other CHAT handlers (moderation, etc.)
    execute first. Skips bot messages and hidden messages.

    :param ctx: The event context with the chat event.
    """
    event = ctx.event

    if event.user.is_bot or not event.is_visible:
        return

    get_manager(ctx.module).count_message(event.message_id)


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

    get_manager(ctx.module).remove_messages(set(event.message_ids))
