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
import shutil
from pathlib import Path

from owlbot.api import ModuleContext, on_setup, on_teardown

from .commands import clip_command, clips_command, delclip_command
from .events import on_stream_started, on_stream_stopped
from .manager import ClipManager, get_manager
from .processing import VideoProcessor
from .repository import ClipRepository
from .routes import (
    clip_page,
    clip_thumbnail,
    clip_video,
    clips_list_page,
    editor_js,
    editor_page,
    editor_preview_video,
    editor_submit,
)

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

    repo = ClipRepository(ctx.storage)
    await repo.setup()

    processor = VideoProcessor(ctx.logger)

    clips_dir = Path(str(ctx.config.get("clips_dir")))
    await asyncio.to_thread(clips_dir.mkdir, parents=True, exist_ok=True)

    manager = ClipManager(ctx, repo, processor, clips_dir)
    ctx.state["manager"] = manager

    # Check if stream is already live.
    try:
        status = await ctx.owncast_client.get_status()
        if status.get("online", False):
            ctx.logger.info("Stream is already live. Starting HLS cache.")
            await manager.start_caching()
    except Exception:  # noqa: BLE001  # best-effort; non-fatal if Owncast is unreachable during setup
        ctx.logger.warning(
            "Could not check Owncast status during setup.", exc_info=True
        )


@on_teardown
async def teardown(ctx: ModuleContext) -> None:
    """Clean up the clips module.

    :param ctx: Module context.
    """
    manager = get_manager(ctx)
    await manager.teardown()
    ctx.state["manager"] = None
