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

"""Clips module for Owlbot.

Allows chat users to create clips from the live Owncast stream. Caches the HLS
stream in real time and provides a browser-based clip editor.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from pathlib import Path

from owlbot.api import (
    EventContext,
    EventType,
    ModuleContext,
    StreamStartedEvent,
    StreamStoppedEvent,
    on_event,
    on_setup,
    on_teardown,
)

from .cache import start_caching, stop_caching
from .commands import clip_command, clips_command, delclip_command
from .processing import ProcessingManager
from .routes import (
    cleanup_session,
    clip_page,
    clip_thumbnail,
    clip_video,
    clips_list_page,
    editor_js,
    editor_page,
    editor_preview_video,
    editor_submit,
)
from .types import ModuleState, get_state

__all__ = [
    "clip_command",
    "clip_page",
    "clip_thumbnail",
    "clip_video",
    "clips_command",
    "clips_list_page",
    "delclip_command",
    "editor_js",
    "editor_page",
    "editor_preview_video",
    "editor_submit",
    "on_stream_started",
    "on_stream_stopped",
    "setup",
    "teardown",
]


@on_setup
async def setup(ctx: ModuleContext) -> None:
    """Initialize the clips module.

    Validates ffmpeg availability, creates the database schema, registers
    config defaults, creates the clips storage directory, and starts caching
    if a stream is already live.

    :param ctx: Module context with config, storage, and other services.
    """
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            msg = (
                f"'{binary}' not found in PATH. "
                f"The clips module requires ffmpeg to be installed."
            )
            raise RuntimeError(msg)

    await ctx.storage.execute("""
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            creator TEXT NOT NULL,
            created_at TEXT NOT NULL,
            duration REAL
        )
    """)

    ctx.config.register_defaults(
        {
            "cache_duration": 300,
            "min_clip_length": 5,
            "max_clip_length": 120,
            "clips_dir": "data/clips",
            "session_expiry": 900,
            "grace_period": 120,
        }
    )

    # Create clips directory if it doesn't exist.
    clips_dir = Path(str(ctx.config.get("clips_dir")))
    clips_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    # Initialize runtime state.
    ctx.state["clips"] = ModuleState()
    module_state = get_state(ctx)
    module_state.manager = ProcessingManager(ctx.logger)

    # Check if stream is already live.
    try:
        status = await ctx.owncast_client.get_status()
        if status.get("online", False):
            ctx.logger.info("Stream is already live. Starting HLS cache.")
            await start_caching(ctx)
    except Exception:
        ctx.logger.warning(
            "Could not check Owncast status during setup.", exc_info=True
        )


@on_event(EventType.STREAM_STARTED)
async def on_stream_started(ctx: EventContext[StreamStartedEvent]) -> None:
    """Start HLS caching when the stream goes live.

    :param ctx: The event context.
    """
    # If there's a grace period task running, cancel it and wait for it to finish.
    module_state = get_state(ctx.module)
    if module_state.grace_task is not None:
        ctx.module.logger.debug("Cancelling active grace period task.")
        module_state.grace_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await module_state.grace_task
        module_state.grace_task = None

    # Wipe old cache before starting fresh.
    await stop_caching(ctx.module)
    await start_caching(ctx.module)


@on_event(EventType.STREAM_STOPPED)
async def on_stream_stopped(ctx: EventContext[StreamStoppedEvent]) -> None:
    """Begin grace period when the stream goes offline.

    :param ctx: The event context.
    """

    async def _grace_period(module_ctx: ModuleContext) -> None:
        grace = int(module_ctx.config.get("grace_period"))
        module_ctx.logger.info(f"Stream stopped. Grace period: {grace}s.")
        await asyncio.sleep(grace)
        await stop_caching(module_ctx)
        module_ctx.logger.info("Grace period ended. Cache cleaned up.")

    ctx.module.logger.debug("Stream stopped event received. Scheduling grace period.")
    get_state(ctx.module).grace_task = asyncio.create_task(_grace_period(ctx.module))


@on_teardown
async def teardown(ctx: ModuleContext) -> None:
    """Clean up the clips module.

    Cancels grace/expiry tasks and cleans up temp files.

    :param ctx: Module context.
    """
    module_state = get_state(ctx)

    # 1. Cancel grace period task if running.
    if module_state.grace_task is not None:
        ctx.logger.debug("Cancelling grace period task during teardown.")
        module_state.grace_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await module_state.grace_task

    # 2. Stop caching.
    await stop_caching(ctx)

    # 3. Cancel all session expiry tasks (prevents races during drain).
    for session in module_state.sessions.values():
        if session.expiry_task is not None:
            session.expiry_task.cancel()

    # 4. Clean up editor sessions and their working directories.
    for token in list(module_state.sessions):
        cleanup_session(module_state.sessions, token)

    ctx.logger.info("Clips module cleaned up.")
