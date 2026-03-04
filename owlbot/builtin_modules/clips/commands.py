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

"""Chat commands for the clips module."""

from __future__ import annotations

import secrets
import shutil
import tempfile
from pathlib import Path

from owlbot.api import CommandContext, on_command

from .routes import schedule_session_expiry
from .types import EditorSession, get_state


@on_command("clip", cooldown=15)
async def clip_command(ctx: CommandContext) -> None:
    """Create a clip from the current stream.

    Snapshots the HLS cache, generates a preview MP4, and sends the user
    a link to the clip editor.

    :param ctx: The command context.
    """
    module = ctx.module
    module_state = get_state(module)

    # Check if stream is live / cache is available.
    min_clip_length = int(ctx.module.config.get("min_clip_length"))
    min_cache_duration = min_clip_length * 2
    if (
        module_state.cache is None
        or module_state.cache.total_duration < min_cache_duration
    ):
        ctx.logger.debug("Clip command rejected: not enough cached data.")
        await ctx.owncast_client.send_message(
            "Not enough stream data is available yet. Please try again later."
        )
        return

    # Snapshot chunks into a working directory.
    work_dir = Path(tempfile.mkdtemp(prefix="owlbot-clip-work-"))
    snapshot_files, snapshot_duration = module_state.cache.snapshot(work_dir)

    ctx.logger.debug(
        f"Snapshot for {ctx.user.display_name}: {len(snapshot_files)} chunks, "
        f"{snapshot_duration:.1f}s into {work_dir}."
    )

    if not snapshot_files:
        shutil.rmtree(work_dir, ignore_errors=True)
        await ctx.owncast_client.send_message("No stream data available for clipping.")
        return

    # Generate preview and send editor link.
    manager = module_state.manager
    preview_path = work_dir / "preview.mp4"

    try:
        duration = await manager.generate_preview(snapshot_files, preview_path)
    except Exception:
        module.logger.error("Failed to generate clip preview.", exc_info=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        await ctx.owncast_client.send_message(
            "Failed to create clip preview. Please try again."
        )
        return

    # Clean up hardlinked chunks (preview is self-contained now).
    for f in snapshot_files:
        if f.exists():
            f.unlink()

    # Create editor session.
    token = secrets.token_urlsafe(32)
    session_expiry = int(module.config.get("session_expiry"))
    session = EditorSession(
        preview_path=preview_path,
        work_dir=work_dir,
        duration=duration,
        creator=ctx.user.display_name,
    )
    module_state.sessions[token] = session
    schedule_session_expiry(
        module_state.sessions,
        token,
        session,
        session_expiry,
        module.logger,
    )

    url = ctx.routes.url_for(f"/edit/{token}")
    await ctx.owncast_client.send_system_message_to_client(
        ctx.chat_event.client_id,
        f"Your clip preview is ready! Edit it here: {url}",
    )

    module.logger.info(
        f"Clip preview generated for {ctx.user.display_name} "
        f"({duration:.1f}s, token={token[:8]}...)"
    )


@on_command("clips", cooldown=15)
async def clips_command(ctx: CommandContext) -> None:
    """Send the URL to the clips list page.

    :param ctx: The command context.
    """
    url = ctx.routes.url_for("/list")
    await ctx.owncast_client.send_message(f"Clips: {url}")


@on_command("delclip", requires_moderator=True)
async def delclip_command(ctx: CommandContext) -> None:
    """Delete a clip by ID. Moderator only.

    :param ctx: The command context.
    """
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message("Usage: !delclip <clip_id>")
        return

    try:
        clip_id = int(args[0])
    except ValueError:
        await ctx.owncast_client.send_message("Usage: !delclip <clip_id>")
        return

    row = await ctx.storage.fetch_one("SELECT id FROM clips WHERE id = ?", (clip_id,))
    if not row:
        ctx.logger.debug(f"Clip {clip_id} not found for deletion.")
        await ctx.owncast_client.send_message(f"Clip {clip_id} not found.")
        return

    # Delete files from disk.
    clips_dir = Path(str(ctx.module.config.get("clips_dir")))
    clip_path = clips_dir / f"{clip_id}.mp4"
    if clip_path.exists():
        clip_path.unlink()
    thumbnail_path = clips_dir / f"{clip_id}.webp"
    if thumbnail_path.exists():
        thumbnail_path.unlink()

    # Delete DB record.
    await ctx.storage.execute("DELETE FROM clips WHERE id = ?", (clip_id,))

    ctx.logger.info(f"Clip {clip_id} deleted by {ctx.user.display_name}.")
    await ctx.owncast_client.send_message(f"Clip {clip_id} deleted.")
