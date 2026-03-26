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
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from .cache import ChunkCache


class ProcessingManager:
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
            if not concat_string:
                msg = "ffmpeg: no cached segments available."
                raise RuntimeError(msg)

            async with self._ffmpeg_semaphore:
                self._logger.debug(
                    f"Generating preview via concat protocol -> {output_path}."
                )
                await self._run_ffprocess(
                    "ffmpeg",
                    "-y",
                    "-i",
                    f"concat:{concat_string}",
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(output_path),
                )

        async with self._ffprobe_semaphore:
            duration = await self._probe_duration(output_path)

        self._logger.debug(f"Preview generated ({duration:.1f}s).")
        return duration

    async def create_clip(
        self,
        preview_path: Path,
        output_path: Path,
        start: float,
        end: float,
    ) -> float:
        """Cut a clip from a preview MP4.

        Acquires the ffmpeg semaphore for the cut step, then the ffprobe
        semaphore for the duration probe.

        :param preview_path: Path to the preview MP4.
        :param output_path: Where to write the final clip MP4.
        :param start: Start time in seconds.
        :param end: End time in seconds.
        :return: Duration of the created clip in seconds.
        :raises RuntimeError: If ffmpeg fails.
        """
        async with self._ffmpeg_semaphore:
            self._logger.debug(
                f"Cutting clip {start:.1f}-{end:.1f}s "
                f"from {preview_path} -> {output_path}."
            )
            await self._run_ffprocess(
                "ffmpeg",
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

        async with self._ffprobe_semaphore:
            duration = await self._probe_duration(output_path)

        self._logger.debug(f"Clip created ({duration:.1f}s).")
        return duration

    async def generate_thumbnail(
        self,
        clip_path: Path,
        output_path: Path,
        *,
        duration: float,
    ) -> None:
        """Extract a single frame from the middle of a clip as a WebP thumbnail.

        Acquires the ffmpeg semaphore for the extraction step.

        :param clip_path: Path to the source MP4 clip.
        :param output_path: Where to write the output WebP image.
        :param duration: Duration of the clip in seconds (used to find midpoint).
        :raises RuntimeError: If ffmpeg fails.
        """
        midpoint = duration / 2

        async with self._ffmpeg_semaphore:
            self._logger.debug(
                f"Generating thumbnail at {midpoint:.1f}s -> {output_path}."
            )
            await self._run_ffprocess(
                "ffmpeg",
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
            )

        self._logger.debug(f"Thumbnail generated: {output_path}.")

    async def _run_ffprocess(
        self,
        *args: str,
    ) -> tuple[bytes, bytes]:
        """Run an ffmpeg/ffprobe subprocess with cancellation safety.

        If the calling coroutine is cancelled (e.g. by a handler timeout),
        the subprocess is killed before re-raising.

        :param args: Command and arguments (e.g. "ffmpeg", "-y", ...).
        :return: Tuple of (stdout, stderr) bytes.
        :raises RuntimeError: If the process returns non-zero.
        """
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
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

    async def _probe_duration(
        self,
        file_path: Path,
    ) -> float:
        """Get the duration of a media file using ffprobe.

        :param file_path: Path to the media file.
        :return: Duration in seconds.
        :raises RuntimeError: If ffprobe fails.
        """
        stdout, _ = await self._run_ffprocess(
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(file_path),
        )

        data = json.loads(stdout)
        return float(data["format"]["duration"])
