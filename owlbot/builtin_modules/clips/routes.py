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

import asyncio
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from owlbot.api import RouteContext, on_route

from .types import EditorSession, get_state

if TYPE_CHECKING:
    import logging

_EDITOR_JS_PATH = Path(__file__).resolve().parent / "static" / "editor.js"


def _validate_clip_params(
    *,
    start: float,
    end: float,
    preview_duration: float,
    min_length: int,
    max_length: int,
) -> str | None:
    """Validate clip start/end parameters.

    :param start: Start time in seconds.
    :param end: End time in seconds.
    :param preview_duration: Duration of the preview in seconds.
    :param min_length: Minimum clip length in seconds.
    :param max_length: Maximum clip length in seconds.
    :return: Error message string, or None if valid.
    """
    if not math.isfinite(start) or not math.isfinite(end):
        return "Invalid start or end time."
    if start < 0:
        return "Start time cannot be negative."
    if end <= start:
        return "End time must be after start time."
    if end > preview_duration:
        return f"End time exceeds preview duration ({preview_duration:.1f}s)."
    duration = end - start
    if duration < min_length:
        return f"Clip is too short. Minimum length is {min_length} seconds."
    if duration > max_length:
        return f"Clip is too long. Maximum length is {max_length} seconds."
    return None


def _get_session(ctx: RouteContext, token: str) -> EditorSession | None:
    """Look up an editor session by token, returning None if missing.

    :param ctx: The route context.
    :param token: The editor session token.
    :return: The session, or None.
    """
    sessions = get_state(ctx.module).sessions
    session = sessions.get(token)
    if session is None:
        ctx.module.logger.debug(
            f"Session lookup failed: token={token[:8]}... not found."
        )
        return None
    return session


def cleanup_session(sessions: dict[str, EditorSession], token: str) -> None:
    """Remove a session and clean up its working directory.

    :param sessions: The sessions dict from module state.
    :param token: The session token to remove.
    """
    session = sessions.pop(token, None)
    if session is not None:
        if session.expiry_task is not None:
            session.expiry_task.cancel()
        shutil.rmtree(session.work_dir, ignore_errors=True)


def schedule_session_expiry(
    sessions: dict[str, EditorSession],
    token: str,
    session: EditorSession,
    delay: float,
    logger: logging.Logger,
) -> None:
    """Schedule a task that cleans up the session after *delay* seconds.

    Any existing expiry task on *session* is cancelled first.

    :param sessions: The sessions dict from module state.
    :param token: The session token.
    :param session: The editor session.
    :param delay: Seconds until expiry.
    :param logger: Logger instance for debug messages.
    """
    if session.expiry_task is not None:
        session.expiry_task.cancel()

    async def _expire() -> None:
        await asyncio.sleep(delay)
        if sessions.get(token) is not session:
            return
        logger.debug(f"Session expired: token={token[:8]}...")
        cleanup_session(sessions, token)

    session.expiry_task = asyncio.create_task(_expire())


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
    session = _get_session(ctx, token)

    if session is None:
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
    session = _get_session(ctx, token)

    if session is None:
        return web.Response(status=404)

    if not session.preview_path.exists():
        return web.Response(status=404)

    return web.FileResponse(session.preview_path)


@on_route("/static/editor.js", methods=["GET"])
async def editor_js(ctx: RouteContext) -> web.StreamResponse:
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

    Validates parameters, processes the clip inline with ffmpeg, and
    redirects to the finished clip page.

    :param ctx: The route context.
    :return: Redirect to the clip page, or error response.
    """
    token = ctx.match_info["token"]
    session = _get_session(ctx, token)

    if session is None:
        return _error_page(ctx, 404, "Session not found or expired.")

    data = await ctx.request.post()

    try:
        start = float(str(data.get("start", "0")))
        end = float(str(data.get("end", "0")))
    except ValueError:
        return _error_page(ctx, 400, "Invalid start or end time.")

    title = str(data.get("title", "")).strip()[:200] or None
    preview_duration = session.duration
    min_length = int(ctx.config.get("min_clip_length"))
    max_length = int(ctx.config.get("max_clip_length"))

    error = _validate_clip_params(
        start=start,
        end=end,
        preview_duration=preview_duration,
        min_length=min_length,
        max_length=max_length,
    )
    if error is not None:
        ctx.logger.debug(f"Clip submit validation failed: {error}")
        return _error_page(ctx, 400, error)

    # Remove session to prevent double-submission.
    sessions = get_state(ctx.module).sessions
    sessions.pop(token, None)
    if session.expiry_task is not None:
        session.expiry_task.cancel()

    clips_dir = Path(str(ctx.config.get("clips_dir")))
    work_dir = session.work_dir
    manager = get_state(ctx.module).manager

    clip_id: int | None = None
    clip_path: Path | None = None

    try:
        # Cut clip from preview.
        temp_clip_path = work_dir / "clip.mp4"
        actual_duration = await manager.create_clip(
            session.preview_path,
            temp_clip_path,
            start,
            end,
        )

        # Insert DB row.
        cursor = await ctx.storage.execute(
            "INSERT INTO clips (title, creator, created_at, duration) "
            "VALUES (?, ?, ?, ?)",
            (title, session.creator, datetime.now(UTC).isoformat(), actual_duration),
        )
        clip_id = cursor.lastrowid
        if clip_id is None:
            msg = "Failed to retrieve last inserted row ID."
            raise RuntimeError(msg)

        # Move to final location.
        clip_path = clips_dir / f"{clip_id}.mp4"
        shutil.move(temp_clip_path, clip_path)

        # Generate thumbnail (best-effort).
        thumbnail_path = clips_dir / f"{clip_id}.webp"
        try:
            await manager.generate_thumbnail(
                clip_path,
                thumbnail_path,
                duration=actual_duration,
            )
        except Exception:
            ctx.logger.warning(
                f"Thumbnail generation failed for clip {clip_id}.",
                exc_info=True,
            )
            thumbnail_path.unlink(missing_ok=True)

        ctx.logger.info(f"Clip {clip_id} ready ({actual_duration:.1f}s).")

        clip_url = ctx.routes.url_for(f"/clip/{clip_id}")
        await ctx.owncast_client.send_message(
            f"{session.creator} created a clip: {clip_url}"
        )

        return web.HTTPFound(clip_url)

    except Exception:
        ctx.logger.error("Failed to create clip.", exc_info=True)

        # Clean up partial state to avoid orphaned DB rows or files.
        if clip_id is not None:
            try:
                await ctx.storage.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            except Exception:
                ctx.logger.warning(
                    f"Failed to clean up DB row for clip {clip_id}.",
                    exc_info=True,
                )
        if clip_path is not None:
            clip_path.unlink(missing_ok=True)

        return _error_page(ctx, 500, "Clip processing failed.")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@on_route("/clip/{clip_id}", methods=["GET"])
async def clip_page(ctx: RouteContext) -> web.Response:
    """Serve an individual clip page.

    :param ctx: The route context.
    :return: HTML response with the clip page.
    """
    try:
        clip_id = int(ctx.match_info["clip_id"])
    except ValueError:
        return _error_page(ctx, 404, "Clip not found.")

    row = await ctx.storage.fetch_one(
        "SELECT id, title, creator, created_at FROM clips WHERE id = ?",
        (clip_id,),
    )
    if row is None:
        return _error_page(ctx, 404, "Clip not found.")

    video_url = ctx.routes.url_for(f"/clip/{clip_id}/video")

    page = ctx.templates.render(
        "clip.html",
        clip_id=row["id"],
        title=row["title"],
        creator=row["creator"],
        created_at=row["created_at"],
        video_url=video_url,
        list_url=ctx.routes.url_for("/list"),
    )

    return web.Response(text=page, content_type="text/html")


@on_route("/clip/{clip_id}/video", methods=["GET"])
async def clip_video(ctx: RouteContext) -> web.StreamResponse:
    """Serve a clip video file.

    :param ctx: The route context.
    :return: The MP4 file as a streaming response.
    """
    try:
        clip_id = int(ctx.match_info["clip_id"])
    except ValueError:
        return web.Response(status=404)

    row = await ctx.storage.fetch_one(
        "SELECT id FROM clips WHERE id = ?",
        (clip_id,),
    )
    if row is None:
        return web.Response(status=404)

    clips_dir = Path(str(ctx.config.get("clips_dir")))
    clip_path = clips_dir / f"{clip_id}.mp4"
    if not clip_path.exists():
        return web.Response(status=404)

    return web.FileResponse(clip_path)


@on_route("/clip/{clip_id}/thumbnail", methods=["GET"])
async def clip_thumbnail(ctx: RouteContext) -> web.StreamResponse:
    """Serve a clip thumbnail image.

    :param ctx: The route context.
    :return: The WebP image as a response, or 404.
    """
    try:
        clip_id = int(ctx.match_info["clip_id"])
    except ValueError:
        return web.Response(status=404)

    row = await ctx.storage.fetch_one(
        "SELECT id FROM clips WHERE id = ?",
        (clip_id,),
    )
    if row is None:
        return web.Response(status=404)

    clips_dir = Path(str(ctx.config.get("clips_dir")))
    thumbnail_path = clips_dir / f"{clip_id}.webp"
    if not thumbnail_path.exists():
        return web.Response(status=404)

    return web.FileResponse(thumbnail_path)


@on_route("/list", methods=["GET"])
async def clips_list_page(ctx: RouteContext) -> web.Response:
    """Serve a page listing all clips.

    :param ctx: The route context.
    :return: HTML response with the clips list.
    """
    rows = await ctx.storage.fetch_all(
        "SELECT id, title, created_at FROM clips ORDER BY created_at DESC"
    )

    clips = [
        {
            "id": row["id"],
            "title": row["title"] or f"Clip #{row['id']}",
            "created_at": datetime.fromisoformat(row["created_at"]).strftime(
                "%B %-d, %Y"
            ),
            "url": ctx.routes.url_for(f"/clip/{row['id']}"),
            "thumbnail_url": ctx.routes.url_for(f"/clip/{row['id']}/thumbnail"),
        }
        for row in rows
    ]

    page = ctx.templates.render("list.html", clips=clips)

    return web.Response(text=page, content_type="text/html")
