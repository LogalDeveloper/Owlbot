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

"""HLS stream caching collaborator for the clips module.

Polls the Owncast HLS variant playlist, downloads new segments, and manages
a rolling window of cached chunks on disk. Has no dependency on the Owlbot
module framework; all external resources are passed in explicitly.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import aiohttp
import m3u8  # type: ignore[import-untyped]

if TYPE_CHECKING:
    import logging
    from collections.abc import AsyncIterator

_SEGMENT_URI_RE = re.compile(r"stream-([A-Za-z0-9_-]+)-(\d+)\.(ts|m4s)$")

_MAX_RETRIES = 10
_RETRY_DELAY = 3.0


class ChunkCache:
    """Manages a rolling window of cached HLS stream segments.

    Uses a set to track downloaded sequence numbers. All segments in a variant
    playlist share the same target duration, so total cache duration is
    derived from the set size and the target duration.
    """

    def __init__(
        self,
        http: aiohttp.ClientSession,
        cache_dir: Path,
        cache_duration: int,
        segment_id: str,
        extension: str,
        target_duration: float,
        variant_url: str,
        logger: logging.Logger,
        init_path: Path | None = None,
    ) -> None:
        """Initialize the chunk cache.

        :param http: HTTP client session for polling.
        :param cache_dir: Directory to store cached segment files.
        :param cache_duration: Maximum cache window in seconds.
        :param segment_id: Unique stream segment identifier (e.g. ``jdofFGg``).
        :param extension: Segment file extension without dot (e.g. ``ts`` or ``m4s``).
        :param target_duration: HLS target segment duration in seconds.
        :param variant_url: URL to the variant m3u8 playlist.
        :param logger: Logger instance.
        :param init_path: Path to fMP4 initialization segment, if applicable.
        """
        self._http = http
        self._cache_dir = cache_dir
        self._cache_duration = cache_duration
        self._segment_id = segment_id
        self._extension = extension
        self._target_duration = target_duration
        self._variant_url = variant_url
        self._logger = logger
        self._init_path = init_path
        self._sequences: set[int] = set()
        self._prune_guard_count: int = 0
        self._task: asyncio.Task[None] | None = None

    @property
    def buffered_duration(self) -> float:
        """Return total duration of cached chunks in seconds."""
        return len(self._sequences) * self._target_duration

    def _segment_path(self, sequence: int) -> Path:
        """Return the file path for a segment by sequence number."""
        return (
            self._cache_dir / f"stream-{self._segment_id}-{sequence}.{self._extension}"
        )

    @asynccontextmanager
    async def suppress_pruning(self) -> AsyncIterator[None]:
        """Context manager that suppresses pruning while reading from the cache.

        Supports nested calls via reference counting.
        """
        self._prune_guard_count += 1
        try:
            yield
        finally:
            self._prune_guard_count -= 1

    def concat_protocol_string(self) -> str:
        """Build the ffmpeg concat protocol input string.

        For fMP4 streams, the initialization segment is prepended.

        :return: Pipe-separated list of cached segment paths.
        """
        parts: list[str] = []
        if self._init_path is not None:
            parts.append(str(self._init_path))
        parts.extend(str(self._segment_path(seq)) for seq in sorted(self._sequences))
        return "|".join(parts)

    def start(self) -> None:
        """Start the background polling task."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._polling_loop(), name="Clips Module - HLS polling loop"
        )

    async def stop(self) -> None:
        """Cancel the internal polling task and clean up cached files."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await asyncio.to_thread(shutil.rmtree, self._cache_dir, ignore_errors=True)
        self._sequences.clear()

    async def _download_segment(self, segment_url: str, seq: int) -> bool:
        """Download a single HLS segment and add it to the cache.

        :param segment_url: URL to download the segment from.
        :param seq: HLS media sequence number.
        :return: True if the segment was downloaded successfully, False otherwise.
        """
        try:
            async with self._http.get(segment_url) as seg_resp:
                if seg_resp.status != HTTPStatus.OK:
                    return False
                data = await seg_resp.read()
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientError:
            self._logger.debug(
                "Network error downloading segment %d.", seq, exc_info=True
            )
            return False

        chunk_path = self._segment_path(seq)
        await asyncio.to_thread(chunk_path.write_bytes, data)

        self._sequences.add(seq)
        self._logger.debug("Cached segment %d (%d bytes).", seq, len(data))
        return True

    async def _polling_loop(self) -> None:
        """Background task that polls the HLS variant playlist for new segments."""
        pending_retries: set[int] = set()

        while True:
            try:
                async with self._http.get(self._variant_url) as resp:
                    if resp.status != HTTPStatus.OK:
                        self._logger.warning(
                            "Variant playlist returned HTTP %d.", resp.status
                        )
                        await asyncio.sleep(self._target_duration)
                        continue
                    content = await resp.text()

                playlist = m3u8.loads(content)

                playlist_segments: dict[int, str] = {}
                for segment in playlist.segments:
                    seg_match = _SEGMENT_URI_RE.search(segment.uri)
                    if seg_match is None:
                        continue
                    playlist_segments[int(seg_match.group(2))] = segment.uri

                abandoned: set[int] = set()
                for seq in list(pending_retries):
                    if seq not in playlist_segments:
                        abandoned.add(seq)
                        continue
                    segment_url = urljoin(self._variant_url, playlist_segments[seq])
                    if await self._download_segment(segment_url, seq):
                        pending_retries.discard(seq)
                        self._logger.info("Segment %d recovered after retry.", seq)

                for seq in abandoned:
                    pending_retries.discard(seq)
                    self._logger.warning(
                        "Segment %d no longer in playlist, giving up.", seq
                    )

                for seq, uri in playlist_segments.items():
                    if seq in self._sequences:
                        continue

                    if seq in pending_retries:
                        continue

                    segment_url = urljoin(self._variant_url, uri)
                    if not await self._download_segment(segment_url, seq):
                        pending_retries.add(seq)
                        self._logger.warning(
                            "Segment %d download failed, queued for retry.", seq
                        )

                if self._prune_guard_count == 0:
                    pre_prune = len(self._sequences)
                    while (
                        self.buffered_duration > self._cache_duration
                        and len(self._sequences) > 1
                    ):
                        oldest = min(self._sequences)
                        self._sequences.discard(oldest)
                        path = self._segment_path(oldest)
                        await asyncio.to_thread(path.unlink, missing_ok=True)
                    if len(self._sequences) < pre_prune:
                        self._logger.debug(
                            "Pruned %d segment(s). Cache: %d chunks, %.1fs total.",
                            pre_prune - len(self._sequences),
                            len(self._sequences),
                            self.buffered_duration,
                        )

            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("Error in HLS polling loop.")

            await asyncio.sleep(self._target_duration)


async def start_caching(
    http: aiohttp.ClientSession,
    base_url: str,
    cache_duration: int,
    logger: logging.Logger,
) -> ChunkCache:
    """Start HLS caching and return the running ChunkCache.

    Fetches the master and variant playlists, constructs the cache, downloads
    the init segment if needed, and launches the background polling task.

    :param http: Shared HTTP client session.
    :param base_url: Base Owncast URL (e.g. ``http://localhost:8080``).
    :param cache_duration: Maximum cache window in seconds.
    :param logger: Logger instance.
    :return: A running ChunkCache with its background polling task active.
    :raises RuntimeError: If initial playlist fetching or setup fails.
    """
    master_url = f"{base_url.rstrip('/')}/hls/stream.m3u8"
    cache_dir = Path(tempfile.mkdtemp(prefix="owlbot-clips-"))

    try:
        master_playlist = await _fetch_playlist(
            http, master_url, "Master playlist", logger
        )
        if master_playlist is None or not master_playlist.playlists:
            msg = "Could not fetch master playlist."
            raise RuntimeError(msg)

        best = max(
            master_playlist.playlists,
            key=lambda p: p.stream_info.bandwidth or 0,
        )
        variant_url = urljoin(master_url, best.uri)
        logger.debug(
            "Selected variant: %s (bandwidth=%s, resolution=%s)",
            variant_url,
            best.stream_info.bandwidth,
            best.stream_info.resolution,
        )

        variant_playlist = await _fetch_playlist(
            http, variant_url, "Variant playlist", logger
        )
        if variant_playlist is None or not variant_playlist.segments:
            msg = "Could not fetch variant playlist."
            raise RuntimeError(msg)

        first_match = _SEGMENT_URI_RE.search(variant_playlist.segments[0].uri)
        if first_match is None:
            msg = "Could not parse segment URI."
            raise RuntimeError(msg)
        segment_id, extension = first_match.group(1), first_match.group(3)

        poll_interval = (
            float(variant_playlist.target_duration)
            if variant_playlist.target_duration
            else 2.0
        )
        logger.debug("Initial poll interval set to %.1fs.", poll_interval)

        init_path: Path | None = None
        if variant_playlist.segment_map:
            init_uri = variant_playlist.segment_map[0].uri
            if init_uri:
                init_url = urljoin(variant_url, init_uri)
                init_filename = init_uri.rsplit("/", 1)[-1]
                init_path = await _download_init_segment(
                    http, cache_dir, init_url, init_filename, logger
                )
                if init_path is None:
                    msg = "Could not download init segment."
                    raise RuntimeError(msg)

        cache = ChunkCache(
            http=http,
            cache_dir=cache_dir,
            cache_duration=cache_duration,
            segment_id=segment_id,
            extension=extension,
            target_duration=poll_interval,
            variant_url=variant_url,
            logger=logger,
            init_path=init_path,
        )

        cache.start()
        logger.info("HLS caching started. Cache dir: %s", cache_dir)

    except BaseException:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise
    else:
        return cache


async def _fetch_playlist(
    http: aiohttp.ClientSession,
    url: str,
    label: str,
    logger: logging.Logger,
) -> m3u8.M3U8 | None:
    """Fetch and parse an HLS playlist with retry logic.

    :param http: HTTP client session.
    :param url: URL of the m3u8 playlist.
    :param label: Human-readable label for log messages.
    :param logger: Logger instance.
    :return: Parsed playlist, or ``None`` if all attempts are exhausted.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        reason: str | None = None
        exc_info = False
        try:
            async with http.get(url) as resp:
                if resp.status != HTTPStatus.OK:
                    reason = f"HTTP {resp.status}"
                else:
                    content = await resp.text()
                    return m3u8.loads(content)
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientError as e:
            reason = str(e)
            exc_info = True
        logger.warning(
            "%s fetch failed (attempt %d/%d): %s. Retrying in %.0fs.",
            label,
            attempt,
            _MAX_RETRIES,
            reason,
            _RETRY_DELAY,
            exc_info=exc_info,
        )
        await asyncio.sleep(_RETRY_DELAY)
    logger.error("%s failed after %d attempts.", label, _MAX_RETRIES)
    return None


async def _download_init_segment(
    http: aiohttp.ClientSession,
    cache_dir: Path,
    init_url: str,
    filename: str,
    logger: logging.Logger,
) -> Path | None:
    """Download the fMP4 initialization segment with retry logic.

    :param http: HTTP client session.
    :param cache_dir: Directory to save the init segment into.
    :param init_url: URL to download the init segment from.
    :param filename: Original filename to save as.
    :param logger: Logger instance.
    :return: Path to the downloaded init segment, or None on failure.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        reason: str | None = None
        exc_info = False
        try:
            async with http.get(init_url) as resp:
                if resp.status != HTTPStatus.OK:
                    reason = f"HTTP {resp.status}"
                else:
                    data = await resp.read()
                    init_path = cache_dir / filename
                    await asyncio.to_thread(init_path.write_bytes, data)
                    logger.debug("Cached init segment (%d bytes).", len(data))
                    return init_path
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientError as e:
            reason = str(e)
            exc_info = True
        logger.warning(
            "Init segment fetch failed (attempt %d/%d): %s. Retrying in %.0fs.",
            attempt,
            _MAX_RETRIES,
            reason,
            _RETRY_DELAY,
            exc_info=exc_info,
        )
        await asyncio.sleep(_RETRY_DELAY)
    logger.error("Init segment failed after %d attempts.", _MAX_RETRIES)
    return None
