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

"""Timers module for Owlbot.

Allows moderators to create recurring chat messages that fire on a configurable
interval with an optional minimum chat line threshold. Supports both simple
duration intervals and cron expressions.
"""

from owlbot.api import ModuleContext, on_setup, on_teardown

# Re-export decorated handlers so the module loader discovers them.
from .commands import (
    addtimer,
    deletetimer,
    disabletimer,
    enabletimer,
    listtimers,
    settimerinterval,
    settimerlines,
    settimermessage,
)
from .events import (
    count_chat_message,
    handle_stream_started,
    handle_stream_stopped,
    handle_visibility_update,
)
from .manager import TimerManager
from .repository import TimerRepository
from .routes import timer_list_page

__all__ = [
    "addtimer",
    "count_chat_message",
    "deletetimer",
    "disabletimer",
    "enabletimer",
    "handle_stream_started",
    "handle_stream_stopped",
    "handle_visibility_update",
    "listtimers",
    "settimerinterval",
    "settimerlines",
    "settimermessage",
    "setup",
    "teardown",
    "timer_list_page",
]


@on_setup
async def setup(ctx: ModuleContext) -> None:
    """Initialize the timers module.

    Creates the database schema, instantiates the TimerManager, and starts
    timers if the stream is currently live.

    :param ctx: Module context with config, storage, and other services.
    """
    repo = TimerRepository(ctx.storage)
    await repo.setup()
    manager = TimerManager(ctx, repo)
    ctx.state["manager"] = manager

    try:
        status = await ctx.owncast_client.get_status()
        if status.get("online", False):
            await manager.start_all()
            ctx.logger.info("Stream is live. Timers started.")
        else:
            ctx.logger.info("Stream is offline. Timers will start on stream start.")
    except Exception:
        ctx.logger.warning(
            "Could not check stream status. Timers will start on stream start.",
            exc_info=True,
        )


@on_teardown
async def teardown(ctx: ModuleContext) -> None:
    """Clean up the timers module.

    Cancels any pending offline stop and stops all running timer tasks.

    :param ctx: Module context.
    """
    manager: TimerManager | None = ctx.state.get("manager")
    if manager is not None:
        manager.cancel_offline_stop()
        await manager.stop_all()
        ctx.state["manager"] = None
