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

"""Video processing manager for the clips module.

Serializes ffmpeg/ffprobe execution via semaphores.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import orjson

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from .cache import ChunkCache


class VideoProcessor:
    """Manages ffmpeg/ffprobe subprocess execution with concurrency control.

    Provides semaphore-gated methods for preview generation, clip
    creation, and thumbnail extraction.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize the processing manager.

        :param logger: Logger instance scoped to the clips module.
        """
        self._logger = logger
        self._ffmpeg_semaphore = asyncio.Semaphore(1)
        self._ffprobe_semaphore = asyncio.Semaphore(1)

    async def generate_preview_from_cache(
        self,
        output_path: Path,
        cache: ChunkCache,
    ) -> float:
        """Generate a preview MP4 from the cached HLS segments.

        Suppresses cache pruning while building the concat string and running
        ffmpeg, then probes the output duration via ffprobe.

        :param output_path: Where to write the output MP4.
        :param cache: The ChunkCache to read segments from.
        :return: Duration of the generated preview in seconds.
        :raises RuntimeError: If no segments are cached.
        """
        async with cache.suppress_pruning():
            concat_string = cache.concat_protocol_string()
            self._logger.debug(
                "Generating preview via concat protocol -> %s.", output_path
            )
            await self._run_ffmpeg(
                "-y",
                "-i",
                f"concat:{concat_string}",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            )

        duration = await self._probe_duration(output_path)
        self._logger.debug("Preview generated (%.1fs).", duration)
        return duration

    async def create_clip(
        self,
        preview_path: Path,
        output_path: Path,
        start: float,
        end: float,
    ) -> float:
        """Cut a clip from a preview MP4.

        Cuts the specified time range via ffmpeg, then probes the
        output duration via ffprobe.

        :param preview_path: Path to the preview MP4.
        :param output_path: Where to write the final clip MP4.
        :param start: Start time in seconds.
        :param end: End time in seconds.
        :return: Duration of the created clip in seconds.
        :raises RuntimeError: If ffmpeg fails.
        """
        self._logger.debug(
            "Cutting clip %.1f-%.1fs from %s -> %s.",
            start,
            end,
            preview_path,
            output_path,
        )
        await self._run_ffmpeg(
            "-y",
            "-ss",
            str(start),
            "-to",
            str(end),
            "-i",
            str(preview_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        )

        duration = await self._probe_duration(output_path)
        self._logger.debug("Clip created (%.1fs).", duration)
        return duration

    async def generate_thumbnail(
        self,
        clip_path: Path,
        output_path: Path,
        *,
        duration: float,
    ) -> None:
        """Extract a single frame from the middle of a clip as a WebP thumbnail.

        :param clip_path: Path to the source MP4 clip.
        :param output_path: Where to write the output WebP image.
        :param duration: Duration of the clip in seconds (used to find midpoint).
        :raises RuntimeError: If ffmpeg fails.
        """
        midpoint = duration / 2

        self._logger.debug(
            "Generating thumbnail at %.1fs -> %s.", midpoint, output_path
        )
        await self._run_ffmpeg(
            "-y",
            "-ss",
            str(midpoint),
            "-i",
            str(clip_path),
            "-frames:v",
            "1",
            "-f",
            "webp",
            str(output_path),
            time_limit=30.0,
        )

        self._logger.debug("Thumbnail generated: %s.", output_path)

    async def _run_ffprocess(
        self,
        *args: str,
        time_limit: float,
    ) -> tuple[bytes, bytes]:
        """Run an ffmpeg/ffprobe subprocess with timeout and cancellation safety.

        If the process exceeds *time_limit* seconds it is killed and a
        `RuntimeError` is raised.  If the calling coroutine is cancelled
        (e.g. by a handler timeout), the subprocess is killed before
        re-raising.

        :param args: Command and arguments (e.g. "ffmpeg", "-y", ...).
        :param time_limit: Maximum seconds to wait for the process.
        :return: Tuple of (stdout, stderr) bytes.
        :raises RuntimeError: If the process returns non-zero or times out.
        """
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=time_limit
            )
        except TimeoutError:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            msg = f"{args[0]} timed out after {time_limit}s"
            raise RuntimeError(msg) from None
        except asyncio.CancelledError:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            raise

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace").strip()
            if stderr_text:
                self._logger.warning("%s stderr:\n%s", args[0], stderr_text)
            msg = f"{args[0]} failed (exit {proc.returncode})"
            raise RuntimeError(msg)

        return stdout, stderr

    async def _run_ffmpeg(
        self,
        *args: str,
        time_limit: float = 120.0,
    ) -> tuple[bytes, bytes]:
        """Run ffmpeg with semaphore gating and a timeout.

        :param args: Arguments passed after ``ffmpeg``.
        :param time_limit: Maximum seconds to wait (default 120).
        :return: Tuple of (stdout, stderr) bytes.
        :raises RuntimeError: If the process fails or times out.
        """
        async with self._ffmpeg_semaphore:
            return await self._run_ffprocess("ffmpeg", *args, time_limit=time_limit)

    async def _run_ffprobe(
        self,
        *args: str,
        time_limit: float = 30.0,
    ) -> tuple[bytes, bytes]:
        """Run ffprobe with semaphore gating and a timeout.

        :param args: Arguments passed after ``ffprobe``.
        :param time_limit: Maximum seconds to wait (default 30).
        :return: Tuple of (stdout, stderr) bytes.
        :raises RuntimeError: If the process fails or times out.
        """
        async with self._ffprobe_semaphore:
            return await self._run_ffprocess("ffprobe", *args, time_limit=time_limit)

    async def _probe_duration(
        self,
        file_path: Path,
    ) -> float:
        """Get the duration of a media file using ffprobe.

        :param file_path: Path to the media file.
        :return: Duration in seconds.
        :raises RuntimeError: If ffprobe fails.
        """
        stdout, _ = await self._run_ffprobe(
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(file_path),
        )
        data = orjson.loads(stdout)
        return float(data["format"]["duration"])
