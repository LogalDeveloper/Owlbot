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

    Uses the concat protocol to read directly from the HLS cache while
    suppressing pruning, then sends the user a link to the clip editor.

    :param ctx: The command context.
    """
    module = ctx.module
    module_state = get_state(module)

    if module_state.cache is None:
        ctx.logger.debug("Clip command rejected: cache is None.")
        await ctx.owncast_client.send_message(
            "Clipping is unavailable for this stream."
        )
        return

    if not module_state.cache.enough_for_clipping:
        ctx.logger.debug("Clip command rejected: not enough cached data.")
        await ctx.owncast_client.send_message(
            "Not enough stream data is available yet. Please try again later."
        )
        return

    cache = module_state.cache

    work_dir = Path(tempfile.mkdtemp(prefix="owlbot-clip-work-"))
    preview_path = work_dir / "preview.mp4"

    try:
        manager = module_state.manager
        duration = await manager.generate_preview_from_cache(preview_path, cache)
    except Exception:
        module.logger.error("Failed to generate clip preview.", exc_info=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        await ctx.owncast_client.send_message(
            "Failed to create clip preview. Please try again."
        )
        return

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
