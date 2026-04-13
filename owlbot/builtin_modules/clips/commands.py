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

from owlbot.api import CommandContext, on_command

from .manager import get_manager
from .types import CacheUnavailableError, ClipNotFoundError, InsufficientCacheError


@on_command("clip", cooldown=15)
async def clip_command(ctx: CommandContext) -> None:
    """Create a clip from the current stream.

    Generates a preview from the HLS cache, creates an editor session,
    and sends the user a link to the clip editor.

    :param ctx: The command context.
    """
    try:
        token = await get_manager(ctx.module).start_editor_session(
            ctx.user.display_name
        )
    except CacheUnavailableError:
        await ctx.owncast_client.send_message(
            "Clipping is unavailable for this stream."
        )
        return
    except InsufficientCacheError:
        await ctx.owncast_client.send_message(
            "Not enough stream data is available yet. Please try again later."
        )
        return
    except Exception:  # noqa: BLE001  # catch-all after specific exceptions; ffmpeg/IO failures are unpredictable
        await ctx.owncast_client.send_message(
            "Failed to create clip preview. Please try again."
        )
        return

    url = ctx.routes.url_for(f"/edit/{token}")
    await ctx.owncast_client.send_system_message_to_client(
        ctx.chat_event.client_id,
        f'<a href="{url}"><u>Click here to create a clip</u></a>.',
        unsanitized=True,
    )


@on_command("clips", cooldown=15)
async def clips_command(ctx: CommandContext) -> None:
    """Send the URL to the clips list page.

    :param ctx: The command context.
    """
    url = ctx.routes.url_for("/list")
    await ctx.owncast_client.send_message(
        f'<a href="{url}"><u>Click here to view clips</u></a>.',
        unsanitized=True,
    )


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

    try:
        await get_manager(ctx.module).delete_clip(clip_id)
    except ClipNotFoundError as e:
        await ctx.owncast_client.send_message(f"Clip {e.clip_id} not found.")
        return

    await ctx.owncast_client.send_message(f"Clip {clip_id} deleted.")
