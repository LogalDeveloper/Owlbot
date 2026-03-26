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
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import m3u8  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from owlbot.api import ModuleContext

from .types import get_state

_PLAYLIST_MAX_RETRIES = 10
_PLAYLIST_RETRY_DELAY = 3.0


class ChunkCache:
    """Manages a rolling window of cached HLS stream segments.

    Uses a set to track downloaded sequence numbers. All segments in a variant
    playlist share the same target duration, so total cache duration is
    derived from the set size and the target duration.
    """

    def __init__(
        self, cache_dir: Path, cache_duration: int, min_clip_length: int
    ) -> None:
        """Initialize the chunk cache.

        :param cache_dir: Directory to store cached .ts files.
        :param cache_duration: Maximum cache window in seconds.
        :param min_clip_length: Minimum clip duration in seconds.
        """
        self._cache_dir = cache_dir
        self._cache_duration = cache_duration
        self._min_clip_length = min_clip_length
        self._sequences: set[int] = set()
        self._target_duration: float = 0.0
        self._prune_guard_count: int = 0

    def __len__(self) -> int:
        """Return the number of cached segments."""
        return len(self._sequences)

    @property
    def total_duration(self) -> float:
        """Return total duration of cached chunks in seconds."""
        return len(self._sequences) * self._target_duration

    @property
    def sequences(self) -> set[int]:
        """Return the set of cached sequence numbers."""
        return self._sequences.copy()

    def set_target_duration(self, duration: float) -> None:
        """Set the target segment duration from the HLS playlist.

        :param duration: Target segment duration in seconds.
        """
        self._target_duration = duration

    def segment_path(self, sequence: int) -> Path:
        """Return the file path for a segment by sequence number.

        :param sequence: HLS media sequence number.
        :return: Path where the segment .ts file should be stored.
        """
        return self._cache_dir / f"segment-{sequence}.ts"

    def has_chunk(self, sequence: int) -> bool:
        """Check if a segment is in the cache.

        :param sequence: HLS media sequence number.
        :return: True if the segment is cached.
        """
        return sequence in self._sequences

    def add_chunk(self, sequence: int) -> None:
        """Add a segment to the cache.

        :param sequence: HLS media sequence number.
        """
        self._sequences.add(sequence)

    def prune(self) -> bool:
        """Remove oldest segments until total duration is within the cache window.

        :return: True if any segments were pruned, False otherwise.
        """
        if self._prune_guard_count > 0:
            return False
        pruned = False
        while self.total_duration > self._cache_duration and len(self._sequences) > 1:
            oldest = min(self._sequences)
            self._sequences.discard(oldest)
            path = self.segment_path(oldest)
            if path.exists():
                path.unlink()
            pruned = True
        return pruned

    @asynccontextmanager
    async def suppress_pruning(self) -> AsyncIterator[None]:
        """Context manager that suppresses pruning while reading from the cache.

        Use as:
            async with cache.suppress_pruning():
                # ffmpeg reads from cache here

        Supports nested calls via reference counting.
        """
        self._prune_guard_count += 1
        try:
            yield
        finally:
            self._prune_guard_count -= 1

    @property
    def enough_for_clipping(self) -> bool:
        """Check if cached data is sufficient for clipping.

        Requires at least 2x the configured min clip length to ensure enough
        buffered data.

        :return: True if total cached duration >= 2 * min_clip_length.
        """
        return self.total_duration >= self._min_clip_length * 2

    def sorted_sequences(self) -> list[int]:
        """Return cached sequence numbers in ascending order.

        :return: Sorted list of sequence numbers.
        """
        return sorted(self._sequences)

    def concat_protocol_string(self) -> str:
        """Build the ffmpeg concat protocol input string.

        :return: Pipe-separated list of cached segment paths.
        """
        return "|".join(str(self.segment_path(seq)) for seq in self.sorted_sequences())

    def cleanup(self) -> None:
        """Remove the cache directory and all its contents."""
        if self._cache_dir.exists():
            shutil.rmtree(self._cache_dir)
        self._sequences.clear()


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
    min_clip_length = int(ctx.config.get("min_clip_length"))
    cache_dir_path = Path(tempfile.mkdtemp(prefix="owlbot-clips-"))

    cache = ChunkCache(
        cache_dir=cache_dir_path,
        cache_duration=cache_duration,
        min_clip_length=min_clip_length,
    )
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


async def _fetch_playlist(
    ctx: ModuleContext,
    url: str,
    label: str,
) -> m3u8.M3U8 | None:
    """Fetch and parse an HLS playlist with retry logic.

    Attempts up to `_PLAYLIST_MAX_RETRIES` fetches with a
    `_PLAYLIST_RETRY_DELAY`-second backoff between failures.
    Propagates `~asyncio.CancelledError` immediately.

    :param ctx: The module context.
    :param url: URL of the m3u8 playlist.
    :param label: Human-readable label for log messages.
    :return: Parsed playlist, or ``None`` if all attempts are exhausted.
    """
    for attempt in range(1, _PLAYLIST_MAX_RETRIES + 1):
        reason: str | None = None
        exc_info = False
        try:
            async with ctx.http.get(url) as resp:
                if resp.status != 200:
                    reason = f"HTTP {resp.status}"
                else:
                    content = await resp.text()
                    return m3u8.loads(content)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            reason = str(e)
            exc_info = True
        ctx.logger.warning(
            f"{label} fetch failed (attempt {attempt}/{_PLAYLIST_MAX_RETRIES}): "
            f"{reason}. Retrying in {_PLAYLIST_RETRY_DELAY:.0f}s.",
            exc_info=exc_info,
        )
        await asyncio.sleep(_PLAYLIST_RETRY_DELAY)
    ctx.logger.error(f"{label} failed after {_PLAYLIST_MAX_RETRIES} attempts.")
    return None


async def _download_segment(
    ctx: ModuleContext,
    cache: ChunkCache,
    segment_url: str,
    seq: int,
) -> bool:
    """Download a single HLS segment and add it to the cache.

    :param ctx: The module context.
    :param cache: The ChunkCache to add the segment to.
    :param segment_url: URL to download the .ts segment from.
    :param seq: HLS media sequence number.
    :return: True if the segment was downloaded successfully, False otherwise.
    """
    try:
        async with ctx.http.get(segment_url) as seg_resp:
            if seg_resp.status != 200:
                return False
            data = await seg_resp.read()
    except Exception:
        ctx.logger.debug(f"Network error downloading segment {seq}.", exc_info=True)
        return False

    chunk_path = cache.segment_path(seq)
    await asyncio.to_thread(chunk_path.write_bytes, data)

    cache.add_chunk(seq)
    ctx.logger.debug(f"Cached segment {seq} ({len(data)} bytes).")
    return True


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
    master_playlist = await _fetch_playlist(ctx, master_url, "Master playlist")
    if master_playlist is None or not master_playlist.playlists:
        ctx.logger.error("Could not fetch master playlist. Caching disabled.")
        module_state = get_state(ctx)
        cache.cleanup()
        module_state.cache = None
        module_state.cache_task = None
        return

    best = max(
        master_playlist.playlists,
        key=lambda p: p.stream_info.bandwidth or 0,
    )
    variant_url = urljoin(master_url, best.uri)
    ctx.logger.debug(
        f"Selected variant: {variant_url} "
        f"(bandwidth={best.stream_info.bandwidth}, "
        f"resolution={best.stream_info.resolution})"
    )

    variant_playlist = await _fetch_playlist(ctx, variant_url, "Variant playlist")
    if variant_playlist is None:
        ctx.logger.error("Could not fetch variant playlist. Caching disabled.")
        module_state = get_state(ctx)
        cache.cleanup()
        module_state.cache = None
        module_state.cache_task = None
        return

    poll_interval = (
        float(variant_playlist.target_duration)
        if variant_playlist.target_duration
        else 2.0
    )
    cache.set_target_duration(poll_interval)
    ctx.logger.debug(f"Initial poll interval set to {poll_interval:.1f}s.")

    pending_retries: set[int] = set()

    while True:
        try:
            async with ctx.http.get(variant_url) as resp:
                if resp.status != 200:
                    ctx.logger.warning(f"Variant playlist returned HTTP {resp.status}.")
                    await asyncio.sleep(poll_interval)
                    continue
                content = await resp.text()

            playlist = m3u8.loads(content)

            media_sequence = playlist.media_sequence or 0

            playlist_sequences: dict[int, str] = {}
            for i, segment in enumerate(playlist.segments):
                playlist_sequences[media_sequence + i] = segment.uri

            abandoned: set[int] = set()
            for seq in list(pending_retries):
                if seq not in playlist_sequences:
                    abandoned.add(seq)
                    continue
                segment_url = urljoin(variant_url, playlist_sequences[seq])
                if await _download_segment(ctx, cache, segment_url, seq):
                    pending_retries.discard(seq)
                    ctx.logger.info(f"Segment {seq} recovered after retry.")

            for seq in abandoned:
                pending_retries.discard(seq)
                ctx.logger.warning(f"Segment {seq} no longer in playlist, giving up.")

            for i, segment in enumerate(playlist.segments):
                seq = media_sequence + i

                if cache.has_chunk(seq):
                    continue

                if seq in pending_retries:
                    continue

                segment_url = urljoin(variant_url, segment.uri)
                if not await _download_segment(ctx, cache, segment_url, seq):
                    pending_retries.add(seq)
                    ctx.logger.warning(
                        f"Segment {seq} download failed, queued for retry."
                    )

            pre_prune_count = len(cache)
            pruned = cache.prune()
            if pruned:
                ctx.logger.debug(
                    f"Pruned {pre_prune_count - len(cache)} segment(s). "
                    f"Cache: {len(cache)} chunks, "
                    f"{cache.total_duration:.1f}s total."
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            ctx.logger.error("Error in HLS polling loop.", exc_info=True)

        await asyncio.sleep(poll_interval)
