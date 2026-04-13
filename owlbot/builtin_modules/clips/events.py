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

"""Event handlers for the clips module."""

from __future__ import annotations

from owlbot.api import (
    EventContext,
    EventType,
    StreamStartedEvent,
    StreamStoppedEvent,
    on_event,
)

from .manager import get_manager


@on_event(EventType.STREAM_STARTED)
async def on_stream_started(ctx: EventContext[StreamStartedEvent]) -> None:
    """Start HLS caching when the stream goes live.

    :param ctx: The event context.
    """
    await get_manager(ctx.module).handle_stream_started()


@on_event(EventType.STREAM_STOPPED)
async def on_stream_stopped(ctx: EventContext[StreamStoppedEvent]) -> None:
    """Begin grace period when the stream goes offline.

    :param ctx: The event context.
    """
    await get_manager(ctx.module).handle_stream_stopped()
