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

"""HLS stream caching subsystem for the clips module.

Polls the Owncast HLS variant playlist, downloads new segments, and manages
a rolling window of cached chunks on disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import m3u8  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from owlbot.api import ModuleContext

from .types import get_state


@dataclass
class ChunkInfo:
    """Metadata for a single cached HLS segment."""

    sequence: int
    duration: float
    path: Path


class ChunkCache:
    """Manages a rolling window of cached HLS stream segments.

    Tracks downloaded chunks and prunes old ones when the total duration
    exceeds the configured cache window.
    """

    def __init__(self, cache_dir: Path, cache_duration: int) -> None:
        """Initialize the chunk cache.

        :param cache_dir: Directory to store cached .ts files.
        :param cache_duration: Maximum cache window in seconds.
        """
        self._cache_dir = cache_dir
        self._cache_duration = cache_duration
        self._chunks: deque[ChunkInfo] = deque()
        self._total_duration = 0.0

    @property
    def chunks(self) -> list[ChunkInfo]:
        """Return the list of cached chunks."""
        return list(self._chunks)

    @property
    def total_duration(self) -> float:
        """Return total duration of cached chunks in seconds."""
        return self._total_duration

    @property
    def last_sequence(self) -> int | None:
        """Return the sequence number of the most recent chunk, or None."""
        if not self._chunks:
            return None
        return self._chunks[-1].sequence

    def segment_path(self, sequence: int) -> Path:
        """Return the file path for a segment by sequence number.

        :param sequence: HLS media sequence number.
        :return: Path where the segment .ts file should be stored.
        """
        return self._cache_dir / f"segment-{sequence}.ts"

    def add_chunk(self, sequence: int, duration: float, path: Path) -> None:
        """Add a downloaded chunk to the cache.

        :param sequence: HLS media sequence number.
        :param duration: Segment duration in seconds.
        :param path: Path to the downloaded .ts file.
        """
        self._chunks.append(ChunkInfo(sequence=sequence, duration=duration, path=path))
        self._total_duration += duration

    def prune(self) -> None:
        """Remove oldest chunks until total duration is within the cache window."""
        while self._total_duration > self._cache_duration and len(self._chunks) > 1:
            old = self._chunks.popleft()
            self._total_duration -= old.duration
            if old.path.exists():
                old.path.unlink()

    def snapshot(self, work_dir: Path) -> tuple[list[Path], float]:
        """Hardlink current chunks into a working directory.

        :param work_dir: Directory to hardlink files into.
        :return: Tuple of (list of hardlinked file paths, total duration).
        """
        files: list[Path] = []
        duration = 0.0

        for chunk in self._chunks:
            if not chunk.path.exists():
                continue
            dest = work_dir / chunk.path.name
            os.link(chunk.path, dest)
            files.append(dest)
            duration += chunk.duration

        return files, duration

    def cleanup(self) -> None:
        """Remove the cache directory and all its contents."""
        if self._cache_dir.exists():
            shutil.rmtree(self._cache_dir)
        self._chunks.clear()
        self._total_duration = 0.0


async def start_caching(ctx: ModuleContext) -> None:
    """Start the HLS caching background task.

    Creates a chunk cache and launches the background polling loop
    that downloads new stream segments.

    :param ctx: The module context.
    """
    module_state = get_state(ctx)
    if module_state.cache is not None:
        ctx.logger.debug("Caching already active, skipping start.")
        return

    owncast_url = ctx.owncast_client.base_url
    master_url = f"{owncast_url.rstrip('/')}/hls/stream.m3u8"
    cache_duration = int(ctx.config.get("cache_duration"))
    cache_dir_path = Path(tempfile.mkdtemp(prefix="owlbot-clips-"))

    cache = ChunkCache(cache_dir=cache_dir_path, cache_duration=cache_duration)
    module_state.cache = cache

    task = asyncio.create_task(
        _polling_loop(ctx, cache, master_url),
    )
    module_state.cache_task = task
    ctx.logger.info(f"HLS caching started. Cache dir: {cache_dir_path}")


async def stop_caching(ctx: ModuleContext) -> None:
    """Stop the HLS caching background task and clean up temp files.

    :param ctx: The module context.
    """
    module_state = get_state(ctx)
    if module_state.cache_task is not None:
        module_state.cache_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await module_state.cache_task
        module_state.cache_task = None

    if module_state.cache is not None:
        module_state.cache.cleanup()
        module_state.cache = None
        ctx.logger.info("HLS cache stopped and cleaned up.")


async def _resolve_variant_url(ctx: ModuleContext, master_url: str) -> str | None:
    """Fetch and parse the master playlist, return the highest bandwidth variant URL.

    :param ctx: The module context.
    :param master_url: URL to the master m3u8 playlist.
    :return: URL to the highest bandwidth variant playlist, or None on failure.
    """
    try:
        async with ctx.http.get(master_url) as resp:
            if resp.status != 200:
                ctx.logger.warning(f"Master playlist returned HTTP {resp.status}.")
                return None
            content = await resp.text()
    except Exception:
        ctx.logger.warning("Failed to fetch master playlist.", exc_info=True)
        return None

    playlist = m3u8.loads(content)

    if not playlist.playlists:
        ctx.logger.warning("No variant playlists found in master playlist.")
        return None

    # Select highest bandwidth variant.
    best = max(
        playlist.playlists,
        key=lambda p: p.stream_info.bandwidth or 0,
    )

    variant_uri: str = best.uri
    # Resolve relative URI against master URL.
    variant_url = urljoin(master_url, variant_uri)
    ctx.logger.debug(
        f"Selected variant: {variant_url} "
        f"(bandwidth={best.stream_info.bandwidth}, "
        f"resolution={best.stream_info.resolution})"
    )
    return variant_url


async def _polling_loop(
    ctx: ModuleContext,
    cache: ChunkCache,
    master_url: str,
) -> None:
    """Background task that polls the HLS variant playlist for new segments.

    :param ctx: The module context.
    :param cache: The ChunkCache instance to populate.
    :param master_url: URL to the master m3u8 playlist.
    """
    max_retries = 10
    variant_url: str | None = None
    for attempt in range(1, max_retries + 1):
        variant_url = await _resolve_variant_url(ctx, master_url)
        if variant_url is not None:
            break
        ctx.logger.warning(
            f"Variant URL resolution failed (attempt {attempt}/{max_retries}). "
            f"Retrying in 3s."
        )
        await asyncio.sleep(3.0)

    if variant_url is None:
        ctx.logger.error(
            f"Could not resolve variant URL after {max_retries} attempts. "
            f"Caching disabled."
        )
        module_state = get_state(ctx)
        cache.cleanup()
        module_state.cache = None
        module_state.cache_task = None
        return

    poll_interval = 2.0  # Default, updated from playlist

    while True:
        try:
            async with ctx.http.get(variant_url) as resp:
                if resp.status != 200:
                    ctx.logger.warning(f"Variant playlist returned HTTP {resp.status}.")
                    await asyncio.sleep(poll_interval)
                    continue
                content = await resp.text()

            playlist = m3u8.loads(content)

            # Update poll interval from target duration.
            if playlist.target_duration:
                new_interval = float(playlist.target_duration)
                if new_interval != poll_interval:
                    ctx.logger.debug(
                        f"Poll interval updated: {poll_interval:.1f}s -> "
                        f"{new_interval:.1f}s."
                    )
                    poll_interval = new_interval

            media_sequence = playlist.media_sequence or 0

            for i, segment in enumerate(playlist.segments):
                seq = media_sequence + i

                # Skip already-downloaded segments.
                last = cache.last_sequence
                if last is not None and seq <= last:
                    continue

                # Download the segment.
                segment_url = urljoin(variant_url, segment.uri)
                try:
                    async with ctx.http.get(segment_url) as seg_resp:
                        if seg_resp.status != 200:
                            ctx.logger.warning(
                                f"Segment {seq} returned HTTP {seg_resp.status}."
                            )
                            continue
                        data = await seg_resp.read()
                except Exception:
                    ctx.logger.warning(
                        f"Failed to download segment {seq}.", exc_info=True
                    )
                    continue

                chunk_path = cache.segment_path(seq)
                await asyncio.to_thread(chunk_path.write_bytes, data)

                cache.add_chunk(
                    sequence=seq,
                    duration=segment.duration,
                    path=chunk_path,
                )
                ctx.logger.debug(
                    f"Cached segment {seq} "
                    f"({segment.duration:.2f}s, {len(data)} bytes)."
                )

            # Prune old segments.
            pre_count = len(cache.chunks)
            cache.prune()
            pruned = pre_count - len(cache.chunks)
            if pruned:
                ctx.logger.debug(
                    f"Pruned {pruned} segment(s). "
                    f"Cache: {len(cache.chunks)} chunks, "
                    f"{cache.total_duration:.1f}s total."
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            ctx.logger.error("Error in HLS polling loop.", exc_info=True)

        await asyncio.sleep(poll_interval)
