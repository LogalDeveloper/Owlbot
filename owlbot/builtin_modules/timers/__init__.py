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

from .chat_counter import count_chat_message, handle_visibility_update

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
from .routes import timer_list_page
from .scheduler import TimerScheduler, clear_scheduler, get_scheduler, set_scheduler

__all__ = [
    "addtimer",
    "count_chat_message",
    "deletetimer",
    "disabletimer",
    "enabletimer",
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
    """
    Initialize the timers module.

    Creates the database schema, initializes chat counters for enabled timers,
    and starts the background scheduler.

    :param ctx: Module context with config, storage, and other services.
    """
    await ctx.storage.execute("""
        CREATE TABLE IF NOT EXISTS timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            message TEXT,
            interval_value TEXT NOT NULL DEFAULT '15m',
            interval_type TEXT NOT NULL DEFAULT 'simple',
            min_chat_lines INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_fired_at TEXT
        )
    """)

    rows = await ctx.storage.fetch_all(
        "SELECT id FROM timers WHERE enabled = 1 AND message IS NOT NULL"
    )
    timer_ids = [row["id"] for row in rows]

    sched = TimerScheduler()
    sched.init_counted_ids(timer_ids)
    ctx.logger.debug("Initialized chat counters for timer IDs: %s", timer_ids)

    count = await ctx.storage.fetch_value("SELECT COUNT(*) FROM timers")
    ctx.logger.info(f"Loaded {count} timer(s) from database.")

    sched.start(ctx)
    set_scheduler(sched)


@on_teardown
async def teardown(ctx: ModuleContext) -> None:
    """
    Clean up the timers module.

    Stops the background scheduler task.

    :param ctx: Module context.
    """
    await get_scheduler().stop(ctx)
    clear_scheduler()
