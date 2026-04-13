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

"""HTTP routes for the clips module."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from aiohttp import web

from owlbot.api import RouteContext, on_route

from .manager import get_manager
from .types import ClipNotFoundError, InvalidClipParamsError, SessionNotFoundError

_EDITOR_JS_PATH = Path(__file__).resolve().parent / "static" / "editor.js"


def _format_date(iso: str) -> str:
    """Format an ISO date string for display.

    :param iso: ISO 8601 date string.
    :return: Human-readable date like "April 13, 2026".
    """
    return datetime.fromisoformat(iso).strftime("%B %-d, %Y")


def _error_page(ctx: RouteContext, status: int, message: str) -> web.Response:
    """Render an error page with navigation back to the clips list.

    :param ctx: The route context.
    :param status: HTTP status code.
    :param message: Error message to display.
    :return: HTML error response.
    """
    page = ctx.templates.render(
        "error.html",
        message=message,
        list_url=ctx.routes.url_for("/list"),
    )
    return web.Response(status=status, text=page, content_type="text/html")


@on_route("/edit/{token}", methods=["GET"])
async def editor_page(ctx: RouteContext) -> web.Response:
    """Serve the clip editor page.

    :param ctx: The route context.
    :return: HTML response with the editor, or error page.
    """
    token = ctx.match_info["token"]
    manager = get_manager(ctx.module)

    try:
        session = manager.get_session(token)
    except SessionNotFoundError:
        return _error_page(ctx, 404, "Session not found or expired.")

    preview_url = ctx.routes.url_for(f"/edit/{token}/preview")
    min_length = int(ctx.config.get("min_clip_length"))
    max_length = int(ctx.config.get("max_clip_length"))
    duration = session.duration

    # Clamp max_length to actual preview duration.
    effective_max = min(max_length, int(duration))

    page = ctx.templates.render(
        "editor.html",
        preview_url=preview_url,
        duration=duration,
        min_length=min_length,
        max_length=effective_max,
        token=token,
        submit_url=ctx.routes.url_for(f"/edit/{token}"),
        list_url=ctx.routes.url_for("/list"),
        editor_js_url=ctx.routes.url_for("/static/editor.js"),
    )

    return web.Response(text=page, content_type="text/html")


@on_route("/edit/{token}/preview", methods=["GET"])
async def editor_preview_video(ctx: RouteContext) -> web.StreamResponse:
    """Serve the preview video file for the clip editor.

    :param ctx: The route context.
    :return: The preview MP4 as a streaming response.
    """
    token = ctx.match_info["token"]

    try:
        session = get_manager(ctx.module).get_session(token)
    except SessionNotFoundError:
        return web.Response(status=404)

    if not session.preview_path.exists():
        return web.Response(status=404)

    return web.FileResponse(session.preview_path)


@on_route("/static/editor.js", methods=["GET"])
async def editor_js(ctx: RouteContext) -> web.StreamResponse:  # noqa: ARG001  # required by route handler signature
    """Serve the clip editor JavaScript.

    :param ctx: The route context.
    :return: The editor.js file with caching headers.
    """
    return web.FileResponse(
        _EDITOR_JS_PATH,
        headers={"Cache-Control": "max-age=86400"},
    )


@on_route("/edit/{token}", methods=["POST"])
async def editor_submit(ctx: RouteContext) -> web.Response:
    """Handle clip editor form submission.

    Validates parameters, processes the clip, and redirects to the
    finished clip page.

    :param ctx: The route context.
    :return: Redirect to the clip page, or error response.
    """
    token = ctx.match_info["token"]
    manager = get_manager(ctx.module)

    data = await ctx.request.post()

    try:
        start = float(str(data.get("start", "0")))
        end = float(str(data.get("end", "0")))
    except ValueError:
        return _error_page(ctx, 400, "Invalid start or end time.")

    title = str(data.get("title", "")).strip()[:200] or None

    try:
        clip = await manager.create_clip(token, start, end, title)
    except SessionNotFoundError:
        return _error_page(ctx, 404, "Session not found or expired.")
    except InvalidClipParamsError as e:
        return _error_page(ctx, 400, e.reason)
    except Exception:
        ctx.logger.exception("Failed to create clip.")
        return _error_page(ctx, 500, "Clip processing failed.")

    clip_url = ctx.routes.url_for(f"/view/{clip.id}")
    await ctx.owncast_client.send_message(f"{clip.creator} created a clip: {clip_url}")

    return web.HTTPFound(clip_url)


@on_route("/view/{clip_id}", methods=["GET"])
async def clip_page(ctx: RouteContext) -> web.Response:
    """Serve an individual clip page.

    :param ctx: The route context.
    :return: HTML response with the clip page.
    """
    try:
        clip_id = int(ctx.match_info["clip_id"])
    except ValueError:
        return _error_page(ctx, 404, "Clip not found.")

    manager = get_manager(ctx.module)
    try:
        clip = await manager.get_clip(clip_id)
    except ClipNotFoundError:
        return _error_page(ctx, 404, "Clip not found.")

    video_url = ctx.routes.url_for(f"/view/{clip_id}/video")

    page = ctx.templates.render(
        "clip.html",
        clip_id=clip.id,
        title=clip.title,
        creator=clip.creator,
        created_at=clip.created_at,
        video_url=video_url,
        list_url=ctx.routes.url_for("/list"),
    )

    return web.Response(text=page, content_type="text/html")


@on_route("/view/{clip_id}/video", methods=["GET"])
async def clip_video(ctx: RouteContext) -> web.StreamResponse:
    """Serve a clip video file.

    :param ctx: The route context.
    :return: The MP4 file as a streaming response.
    """
    try:
        clip_id = int(ctx.match_info["clip_id"])
    except ValueError:
        return web.Response(status=404)

    manager = get_manager(ctx.module)
    try:
        await manager.get_clip(clip_id)
    except ClipNotFoundError:
        return web.Response(status=404)

    clip_path = manager.clips_dir / f"{clip_id}.mp4"
    if not clip_path.exists():
        return web.Response(status=404)

    return web.FileResponse(clip_path)


@on_route("/view/{clip_id}/thumbnail", methods=["GET"])
async def clip_thumbnail(ctx: RouteContext) -> web.StreamResponse:
    """Serve a clip thumbnail image.

    :param ctx: The route context.
    :return: The WebP image as a response, or 404.
    """
    try:
        clip_id = int(ctx.match_info["clip_id"])
    except ValueError:
        return web.Response(status=404)

    manager = get_manager(ctx.module)
    try:
        await manager.get_clip(clip_id)
    except ClipNotFoundError:
        return web.Response(status=404)

    thumbnail_path = manager.clips_dir / f"{clip_id}.webp"
    if not thumbnail_path.exists():
        return web.Response(status=404)

    return web.FileResponse(thumbnail_path)


@on_route("/list", methods=["GET"])
async def clips_list_page(ctx: RouteContext) -> web.Response:
    """Serve a page listing all clips.

    :param ctx: The route context.
    :return: HTML response with the clips list.
    """
    manager = get_manager(ctx.module)
    clips = await manager.list_clips()

    page = ctx.templates.render(
        "list.html",
        clips=clips,
        url_for=ctx.routes.url_for,
        format_date=_format_date,
    )

    return web.Response(text=page, content_type="text/html")
