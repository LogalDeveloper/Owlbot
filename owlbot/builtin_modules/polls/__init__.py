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

"""Polls module for Owlbot.

Allows moderators to create interactive polls in chat with live results
via Server-Sent Events. Supports single and multi-select polls, hidden
results, and authentication requirements.
"""

from __future__ import annotations

from markupsafe import escape

from owlbot.api import (
    EventContext,
    EventType,
    ModuleContext,
    StreamStartedEvent,
    StreamStoppedEvent,
    UserJoinedEvent,
    on_event,
    on_setup,
    on_teardown,
)

from .commands import (
    cancel_poll_command,
    end_poll_command,
    handle_bare_vote,
    poll_command,
    vote_command,
)
from .manager import PollManager, get_manager
from .routes import (
    create_page,
    create_submit,
    events_stream,
    mod_cancel,
    mod_end,
    results_page,
    static_create_js,
    static_polls_css,
    static_polls_js,
    vote_page,
    vote_submit,
)

__all__ = [
    "cancel_poll_command",
    "create_page",
    "create_submit",
    "end_poll_command",
    "events_stream",
    "handle_bare_vote",
    "mod_cancel",
    "mod_end",
    "on_stream_started",
    "on_stream_stopped",
    "on_user_joined",
    "poll_command",
    "results_page",
    "setup",
    "static_create_js",
    "static_polls_css",
    "static_polls_js",
    "teardown",
    "vote_command",
    "vote_page",
    "vote_submit",
]


@on_setup
async def setup(ctx: ModuleContext) -> None:
    """Initialize the polls module.

    :param ctx: Module context with config, storage, and other services.
    """
    ctx.state["manager"] = PollManager(ctx)


@on_event(EventType.STREAM_STOPPED)
async def on_stream_stopped(ctx: EventContext[StreamStoppedEvent]) -> None:
    """Begin a grace period when the stream goes offline.

    :param ctx: The event context.
    """
    get_manager(ctx.module).begin_stream_grace()


@on_event(EventType.STREAM_STARTED)
async def on_stream_started(ctx: EventContext[StreamStartedEvent]) -> None:
    """Cancel the stream grace period if the stream restarts.

    :param ctx: The event context.
    """
    get_manager(ctx.module).cancel_stream_grace()


@on_event(EventType.USER_JOINED)
async def on_user_joined(ctx: EventContext[UserJoinedEvent]) -> None:
    """Notify a user joining chat that a poll is active.

    Sends a private message with the poll question, options, and voting
    instructions.

    :param ctx: The event context.
    """
    manager = get_manager(ctx.module)
    poll = manager.active_poll
    if poll is None:
        return

    escaped_question = escape(poll.question)
    option_lines = "".join(
        f"<strong>{i}.</strong> {escape(opt)}<br>"
        for i, opt in enumerate(poll.options, 1)
    )

    if poll.allow_multiple:
        instructions = "Use <strong>!vote</strong> to get a voting link."
    else:
        instructions = "Type a number in chat or use <strong>!vote</strong> to vote."

    await ctx.owncast_client.send_system_message_to_client(
        ctx.event.client_id,
        f"<h3>Poll in progress: {escaped_question}</h3>"
        f"{option_lines}"
        f"<br>{instructions}",
        unsanitized=True,
    )


@on_teardown
async def teardown(ctx: ModuleContext) -> None:
    """Clean up the polls module.

    :param ctx: Module context.
    """
    manager = get_manager(ctx)
    await manager.teardown()
