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

"""Manager layer for the clips module.

Central coordinator that owns runtime state and enforces all business rules.
Delegates persistence to the repository, video processing to the
VideoProcessor, and HLS caching to the ChunkCache collaborator.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .cache import start_caching
from .types import (
    CacheUnavailableError,
    Clip,
    InsufficientCacheError,
    InvalidClipParamsError,
    SessionNotFoundError,
)

if TYPE_CHECKING:
    from owlbot.api import ModuleContext

    from .cache import ChunkCache
    from .processing import VideoProcessor
    from .repository import ClipRepository


def validate_clip_params(
    *,
    start: float,
    end: float,
    preview_duration: float,
    min_length: int,
    max_length: int,
) -> None:
    """Validate clip start/end parameters.

    :param start: Start time in seconds.
    :param end: End time in seconds.
    :param preview_duration: Duration of the preview in seconds.
    :param min_length: Minimum clip length in seconds.
    :param max_length: Maximum clip length in seconds.
    :raises InvalidClipParamsError: If any constraint is violated.
    """
    if not math.isfinite(start) or not math.isfinite(end):
        raise InvalidClipParamsError("Invalid start or end time.")
    if start < 0:
        raise InvalidClipParamsError("Start time cannot be negative.")
    if end <= start:
        raise InvalidClipParamsError("End time must be after start time.")
    if end > preview_duration:
        raise InvalidClipParamsError(
            f"End time exceeds preview duration ({preview_duration:.1f}s)."
        )
    duration = end - start
    if duration < min_length:
        raise InvalidClipParamsError(
            f"Clip is too short. Minimum length is {min_length} seconds."
        )
    if duration > max_length:
        raise InvalidClipParamsError(
            f"Clip is too long. Maximum length is {max_length} seconds."
        )


@dataclass(slots=True)
class EditorSession:
    """A clip editor session.

    Not frozen because expiry_task is mutable. Internal to the manager.
    """

    preview_path: Path
    work_dir: Path
    duration: float
    creator: str
    expiry_task: asyncio.Task[None] | None = None


class ClipManager:
    """Central coordinator for the clips module.

    :param ctx: Module context with config, logger, and HTTP client.
    :param repo: Clip persistence layer.
    :param processor: ffmpeg/ffprobe collaborator.
    :param clips_dir: Directory for persisted clip files.
    """

    def __init__(
        self,
        ctx: ModuleContext,
        repo: ClipRepository,
        processor: VideoProcessor,
        clips_dir: Path,
    ) -> None:
        """Initialize the ClipManager.

        :param ctx: Module context with config, logger, and HTTP client.
        :param repo: Clip persistence layer.
        :param processor: ffmpeg/ffprobe collaborator.
        :param clips_dir: Directory for persisted clip files.
        """
        self._ctx = ctx
        self._repo = repo
        self._processor = processor
        self._clips_dir = clips_dir
        self._sessions: dict[str, EditorSession] = {}
        self._cache: ChunkCache | None = None
        self._grace_task: asyncio.Task[None] | None = None

    @property
    def clips_dir(self) -> Path:
        """Directory where persisted clip files are stored."""
        return self._clips_dir

    async def get_clip(self, clip_id: int) -> Clip:
        """Fetch a clip by ID.

        :param clip_id: The clip's primary key.
        :return: The Clip snapshot.
        :raises ClipNotFoundError: If no clip with that ID exists.
        """
        return await self._repo.get(clip_id)

    async def delete_clip(self, clip_id: int) -> Clip:
        """Delete a clip, its video file, and its thumbnail.

        :param clip_id: The clip's primary key.
        :return: The deleted Clip snapshot.
        :raises ClipNotFoundError: If no clip with that ID exists.
        """
        clip = await self._repo.delete(clip_id)
        clip_path = self._clips_dir / f"{clip.id}.mp4"
        if clip_path.exists():
            clip_path.unlink()
        thumbnail_path = self._clips_dir / f"{clip.id}.webp"
        if thumbnail_path.exists():
            thumbnail_path.unlink()
        self._ctx.logger.info("Clip %d deleted.", clip.id)
        return clip

    async def list_clips(self) -> list[Clip]:
        """Return all clips ordered by creation date descending.

        :return: List of Clip snapshots.
        """
        return await self._repo.list_all()

    async def start_editor_session(self, creator: str) -> str:
        """Generate a preview from the HLS cache and create an editor session.

        :param creator: Display name of the user creating the clip.
        :return: The editor session token.
        :raises CacheUnavailableError: If no HLS cache is active.
        :raises InsufficientCacheError: If not enough data is cached.
        """
        if self._cache is None:
            raise CacheUnavailableError
        min_clip_length = int(self._ctx.config.get("min_clip_length"))
        if self._cache.buffered_duration < min_clip_length * 2:
            raise InsufficientCacheError

        work_dir = Path(tempfile.mkdtemp(prefix="owlbot-clip-work-"))
        preview_path = work_dir / "preview.mp4"

        try:
            duration = await self._processor.generate_preview_from_cache(
                preview_path, self._cache
            )
        except Exception:
            self._ctx.logger.exception("Failed to generate clip preview.")
            await asyncio.to_thread(shutil.rmtree, work_dir, ignore_errors=True)
            raise

        token = secrets.token_urlsafe(32)
        session = EditorSession(
            preview_path=preview_path,
            work_dir=work_dir,
            duration=duration,
            creator=creator,
        )
        self._sessions[token] = session

        delay = int(self._ctx.config.get("session_expiry"))

        async def _expire() -> None:
            await asyncio.sleep(delay)
            if self._sessions.get(token) is not session:
                return
            self._ctx.logger.debug("Session expired: token=%s...", token[:8])
            await self.cleanup_session(token)

        session.expiry_task = asyncio.create_task(
            _expire(), name=f"Clips Module - Session expiry ({token[:8]})"
        )

        self._ctx.logger.info(
            "Editor session started for %s (token=%s...).",
            creator,
            token[:8],
        )
        return token

    def get_session(self, token: str) -> EditorSession:
        """Look up an editor session by token.

        :param token: The editor session token.
        :return: The EditorSession.
        :raises SessionNotFoundError: If the token is invalid or expired.
        """
        session = self._sessions.get(token)
        if session is None:
            raise SessionNotFoundError(token)
        return session

    async def create_clip(
        self,
        token: str,
        start: float,
        end: float,
        title: str | None,
    ) -> Clip:
        """Finalize a clip from an editor session.

        Validates parameters, cuts the clip, persists metadata, generates a
        thumbnail, and cleans up the session.

        :param token: The editor session token.
        :param start: Start time in seconds.
        :param end: End time in seconds.
        :param title: Optional clip title.
        :return: The created Clip snapshot.
        :raises SessionNotFoundError: If the token is invalid or expired.
        :raises InvalidClipParamsError: If clip parameters are invalid.
        """
        session = self.get_session(token)

        validate_clip_params(
            start=start,
            end=end,
            preview_duration=session.duration,
            min_length=int(self._ctx.config.get("min_clip_length")),
            max_length=int(self._ctx.config.get("max_clip_length")),
        )

        # Remove session to prevent double-submission.
        self._sessions.pop(token, None)
        if session.expiry_task is not None:
            session.expiry_task.cancel()

        clip_id: int | None = None
        clip_path: Path | None = None

        try:
            temp_clip_path = session.work_dir / "clip.mp4"
            actual_duration = await self._processor.create_clip(
                session.preview_path,
                temp_clip_path,
                start,
                end,
            )

            clip = await self._repo.create(
                title=title,
                creator=session.creator,
                created_at=datetime.now(UTC).isoformat(),
                duration=actual_duration,
            )
            clip_id = clip.id

            clip_path = self._clips_dir / f"{clip.id}.mp4"
            await asyncio.to_thread(shutil.move, temp_clip_path, clip_path)

            # Generate thumbnail (best-effort).
            thumbnail_path = self._clips_dir / f"{clip.id}.webp"
            try:
                await self._processor.generate_thumbnail(
                    clip_path,
                    thumbnail_path,
                    duration=actual_duration,
                )
            except Exception:  # best-effort; clip already saved, thumbnail is optional
                self._ctx.logger.warning(
                    "Thumbnail generation failed for clip %d.",
                    clip.id,
                    exc_info=True,
                )
                thumbnail_path.unlink(missing_ok=True)

        except Exception:
            # Clean up partial state to avoid orphaned DB rows or files.
            if clip_id is not None:
                try:
                    await self._repo.delete(clip_id)
                except Exception:  # last-resort; cleanup itself failed, just log it
                    self._ctx.logger.warning(
                        "Failed to clean up DB row for clip %d.",
                        clip_id,
                        exc_info=True,
                    )
            if clip_path is not None:
                clip_path.unlink(missing_ok=True)
            raise
        else:
            return clip
        finally:
            await asyncio.to_thread(shutil.rmtree, session.work_dir, ignore_errors=True)

    async def cleanup_session(self, token: str) -> None:
        """Remove a session and clean up its working directory.

        :param token: The session token to remove.
        """
        session = self._sessions.pop(token, None)
        if session is not None:
            if session.expiry_task is not None:
                session.expiry_task.cancel()
            await asyncio.to_thread(shutil.rmtree, session.work_dir, ignore_errors=True)

    async def start_caching(self) -> None:
        """Start HLS caching from the Owncast stream.

        If caching is already active, this is a no-op.
        """
        if self._cache is not None:
            self._ctx.logger.debug("Caching already active, skipping start.")
            return
        try:
            self._cache = await start_caching(
                http=self._ctx.http.session,
                base_url=self._ctx.owncast_client.base_url,
                cache_duration=int(self._ctx.config.get("cache_duration")),
                logger=self._ctx.logger,
            )
        except Exception:
            self._ctx.logger.exception("Failed to start HLS caching.")

    async def stop_caching(self) -> None:
        """Stop HLS caching and clean up cached files."""
        if self._cache is not None:
            await self._cache.stop()
            self._cache = None

    async def handle_stream_started(self) -> None:
        """Handle a stream-started event: cancel grace period and restart cache."""
        if self._grace_task is not None:
            self._ctx.logger.debug("Cancelling active grace period task.")
            self._grace_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._grace_task
            self._grace_task = None

        await self.stop_caching()
        await self.start_caching()

    async def handle_stream_stopped(self) -> None:
        """Handle a stream-stopped event: schedule grace period before cleanup."""
        if self._grace_task is not None:
            self._ctx.logger.debug("Cancelling active grace period task.")
            self._grace_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._grace_task
            self._grace_task = None

        async def _grace_period() -> None:
            grace = int(self._ctx.config.get("grace_period"))
            self._ctx.logger.info("Stream stopped. Grace period: %ds.", grace)
            await asyncio.sleep(grace)
            await self.stop_caching()
            self._ctx.logger.info("Grace period ended. Cache cleaned up.")

        self._ctx.logger.debug(
            "Stream stopped event received. Scheduling grace period."
        )
        self._grace_task = asyncio.create_task(
            _grace_period(), name="Clips Module - Stream grace period"
        )

    async def teardown(self) -> None:
        """Clean up all module state: grace task, cache, and sessions."""
        # 1. Cancel grace period task if running.
        if self._grace_task is not None:
            self._ctx.logger.debug("Cancelling grace period task during teardown.")
            self._grace_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._grace_task

        # 2. Stop caching.
        await self.stop_caching()

        # 3. Cancel all session expiry tasks (prevents races during drain).
        for session in self._sessions.values():
            if session.expiry_task is not None:
                session.expiry_task.cancel()

        # 4. Clean up editor sessions and their working directories.
        for token in list(self._sessions):
            await self.cleanup_session(token)

        self._ctx.logger.info("Clips module cleaned up.")


def get_manager(ctx: ModuleContext) -> ClipManager:
    """Retrieve the ClipManager from the module context.

    :param ctx: The module context.
    :return: The ClipManager instance.
    :raises RuntimeError: If ClipManager has not been initialized.
    """
    manager = ctx.state.get("manager")
    if not isinstance(manager, ClipManager):
        msg = "ClipManager is not initialized."
        raise RuntimeError(msg)  # noqa: TRY004  # state error, not a type error
    return manager
