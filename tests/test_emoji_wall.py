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

"""Tests for the emoji wall built-in module."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

import orjson
import pytest

if TYPE_CHECKING:
    from aiohttp import web

from owlbot.builtin_modules.emoji_wall import (
    _broadcast,
    _extract_emojis,
    _resolve_direction,
    _resolve_settings,
)


def _img(src: str, class_: str = "emoji") -> str:
    """Build an ``<img>`` tag matching Owncast's emote rendering."""
    return f'<img src="{src}" class="{class_}" />'


class _FakeStreamResponse:
    """Minimal stand-in for ``web.StreamResponse`` that records writes."""

    def __init__(self, *, fail: bool = False) -> None:
        self.written: list[bytes] = []
        self._fail = fail

    async def write(self, data: bytes) -> None:
        """Record *data* or raise if this client is configured to fail."""
        if self._fail:
            raise ConnectionResetError("client gone")
        self.written.append(data)


_URL = "https://stream.example.com"


class TestExtractEmojis:
    """Tests ``_extract_emojis`` from the emoji wall module."""

    @pytest.mark.parametrize(
        ("raw_body", "expected"),
        [
            pytest.param("no emojis here", [], id="plain-text"),
            pytest.param(
                "hello 🔥 world",
                [{"type": "unicode", "emoji": "🔥"}],
                id="single-emoji",
            ),
            pytest.param(
                "🎉🎊✨",
                [
                    {"type": "unicode", "emoji": "🎉"},
                    {"type": "unicode", "emoji": "🎊"},
                    {"type": "unicode", "emoji": "✨"},
                ],
                id="multiple-emojis",
            ),
            pytest.param(
                "hi 👋🏽",
                [{"type": "unicode", "emoji": "👋🏽"}],
                id="skin-tone-modifier",
            ),
            pytest.param(
                "family 👨‍👩‍👧‍👦 time",
                [{"type": "unicode", "emoji": "👨‍👩‍👧‍👦"}],
                id="zwj-sequence",
            ),
            pytest.param(
                "flag 🇺🇸 here",
                [{"type": "unicode", "emoji": "🇺🇸"}],
                id="flag-sequence",
            ),
            pytest.param("", [], id="empty-string"),
        ],
    )
    def test_unicode(self, raw_body: str, expected: list[dict[str, str]]) -> None:
        """Unicode emojis are extracted from the raw body."""
        assert _extract_emojis(raw_body, "", _URL) == expected

    def test_custom_emote(self) -> None:
        """Custom emote img tags are resolved against the Owncast URL."""
        html = _img("/img/emoji/catjam.gif")
        result = _extract_emojis("", html, _URL)
        assert result == [
            {"type": "emote", "url": f"{_URL}/img/emoji/catjam.gif"},
        ]

    def test_multiple_custom_emotes(self) -> None:
        """Multiple emotes in a single message are all extracted."""
        html = _img("/img/emoji/a.gif") + _img("/img/emoji/b.gif")
        result = _extract_emojis("", html, _URL)
        assert result == [
            {"type": "emote", "url": f"{_URL}/img/emoji/a.gif"},
            {"type": "emote", "url": f"{_URL}/img/emoji/b.gif"},
        ]

    def test_non_emoji_img_ignored(self) -> None:
        """An <img> without the emoji class is not matched."""
        html = '<img src="/photo.jpg" class="avatar" />'
        assert _extract_emojis("", html, _URL) == []

    def test_class_before_src(self) -> None:
        """Handles <img> where class appears before src."""
        html = '<img class="emoji" src="/img/emoji/wave.gif" />'
        result = _extract_emojis("", html, _URL)
        assert result == [
            {"type": "emote", "url": f"{_URL}/img/emoji/wave.gif"},
        ]

    def test_extra_class_ignored(self) -> None:
        """An <img> with a class other than exactly 'emoji' is not matched."""
        html = '<img src="/img/emoji/wave.gif" class="emoji custom" />'
        assert _extract_emojis("", html, _URL) == []

    def test_img_without_src_ignored(self) -> None:
        """An <img> with class emoji but no src is not matched."""
        html = '<img class="emoji" />'
        assert _extract_emojis("", html, _URL) == []

    def test_leading_slashes_deduped(self) -> None:
        """Extra leading slashes on the src don't double up."""
        html = _img("///img/emoji/test.gif")
        result = _extract_emojis("", html, _URL)
        assert result == [
            {"type": "emote", "url": f"{_URL}/img/emoji/test.gif"},
        ]

    def test_unicode_and_custom_combined(self) -> None:
        """Unicode emojis and custom emotes are returned together."""
        html = f"<p>🔥 {_img('/img/emoji/catjam.gif')}</p>"
        result = _extract_emojis("🔥 :catjam:", html, _URL)
        assert result == [
            {"type": "unicode", "emoji": "🔥"},
            {"type": "emote", "url": f"{_URL}/img/emoji/catjam.gif"},
        ]

    def test_empty_message(self) -> None:
        """Empty raw body and HTML body returns an empty list."""
        assert _extract_emojis("", "", _URL) == []


class TestResolveDirection:
    """Tests ``_resolve_direction`` from the emoji wall module."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("up", 0, id="preset-up"),
            pytest.param("down", 180, id="preset-down"),
            pytest.param("left", 270, id="preset-left"),
            pytest.param("right", 90, id="preset-right"),
            pytest.param("up-right", 45, id="preset-up-right"),
            pytest.param("down-left", 225, id="preset-down-left"),
            pytest.param("UP", 0, id="case-insensitive"),
            pytest.param("  Down  ", 180, id="whitespace-trimmed"),
            pytest.param("random", "random", id="random-string"),
            pytest.param("RANDOM", "random", id="random-case-insensitive"),
            pytest.param("45", 45, id="numeric-string"),
            pytest.param("0", 0, id="zero-string"),
            pytest.param("359", 359, id="max-degrees-string"),
            pytest.param("nonsense", 0, id="invalid-string-fallback"),
            pytest.param(90, 90, id="int-value"),
            pytest.param(0, 0, id="int-zero"),
            pytest.param(359, 359, id="int-max"),
            pytest.param(-10, 0, id="negative-clamped"),
            pytest.param(500, 359, id="over-max-clamped"),
            pytest.param("999", 359, id="numeric-string-clamped-high"),
            pytest.param("-5", 0, id="numeric-string-clamped-low"),
        ],
    )
    def test_resolution(self, value: str | int, expected: int | str) -> None:
        """Direction values resolve to the expected degree or 'random'."""
        assert _resolve_direction(value) == expected


class TestResolveSettings:
    """Tests ``_resolve_settings`` from the emoji wall module."""

    _DEFAULTS: ClassVar[dict[str, object]] = {
        "min_size": 20,
        "max_size": 60,
        "min_duration": 3,
        "max_duration": 10,
        "max_count": 50,
        "direction": "up",
        "max_rotation": 25,
    }

    def test_defaults(self) -> None:
        """Config defaults produce expected settings."""
        result = _resolve_settings(self._DEFAULTS, {})
        assert result == {
            "min_size": 20,
            "max_size": 60,
            "min_duration": 3,
            "max_duration": 10,
            "max_count": 50,
            "direction": 0,
            "max_rotation": 25,
        }

    def test_query_overrides_config(self) -> None:
        """Query parameters take precedence over config values."""
        result = _resolve_settings(
            self._DEFAULTS,
            {"min_size": "30", "max_size": "80"},
        )
        assert result["min_size"] == 30
        assert result["max_size"] == 80

    def test_invalid_query_falls_back_to_config(self) -> None:
        """Unparsable query values fall back to config."""
        result = _resolve_settings(
            self._DEFAULTS,
            {"min_size": "not_a_number"},
        )
        assert result["min_size"] == 20

    def test_min_max_size_swapped(self) -> None:
        """Inverted min/max sizes are swapped."""
        result = _resolve_settings(
            {**self._DEFAULTS, "min_size": 100, "max_size": 10},
            {},
        )
        assert result["min_size"] == 10
        assert result["max_size"] == 100

    def test_min_max_duration_swapped(self) -> None:
        """Inverted min/max durations are swapped."""
        result = _resolve_settings(
            {**self._DEFAULTS, "min_duration": 30, "max_duration": 1},
            {},
        )
        assert result["min_duration"] == 1
        assert result["max_duration"] == 30

    @pytest.mark.parametrize(
        ("key", "value", "expected"),
        [
            pytest.param("min_size", -10, 1, id="size-floor"),
            pytest.param("max_size", 9999, 500, id="size-ceiling"),
            pytest.param("min_duration", -1, 1, id="duration-floor"),
            pytest.param("max_duration", 999, 60, id="duration-ceiling"),
            pytest.param("max_count", -1, 1, id="count-floor"),
            pytest.param("max_count", 5000, 1000, id="count-ceiling"),
            pytest.param("max_rotation", -5, 0, id="rotation-floor"),
            pytest.param("max_rotation", 999, 360, id="rotation-ceiling"),
        ],
    )
    def test_clamping(self, key: str, value: int, expected: int) -> None:
        """Values outside allowed ranges are clamped."""
        result = _resolve_settings({**self._DEFAULTS, key: value}, {})
        assert result[key] == expected

    def test_direction_random(self) -> None:
        """The 'random' direction string passes through."""
        result = _resolve_settings(
            {**self._DEFAULTS, "direction": "random"},
            {},
        )
        assert result["direction"] == "random"

    def test_direction_query_override(self) -> None:
        """Query parameter overrides the config direction."""
        result = _resolve_settings(self._DEFAULTS, {"direction": "down"})
        assert result["direction"] == 180

    def test_empty_config_uses_zero_defaults(self) -> None:
        """Missing config keys produce clamped-to-minimum values."""
        result = _resolve_settings({}, {})
        assert result["min_size"] == 1
        assert result["max_size"] == 1
        assert result["min_duration"] == 1
        assert result["max_duration"] == 1
        assert result["max_count"] == 1
        assert result["max_rotation"] == 0


class TestBroadcast:
    """Tests ``_broadcast`` from the emoji wall module."""

    async def test_sends_to_all_clients(self) -> None:
        """Payload is written to every connected client."""
        c1 = _FakeStreamResponse()
        c2 = _FakeStreamResponse()
        clients: set[web.StreamResponse] = {c1, c2}  # type: ignore[arg-type]
        events = [{"type": "unicode", "emoji": "🔥"}]

        await _broadcast(clients, events, logging.getLogger("test"))

        expected = b"data: " + orjson.dumps(events[0]) + b"\n\n"
        assert c1.written == [expected]
        assert c2.written == [expected]
        assert len(clients) == 2

    async def test_failed_client_removed(self) -> None:
        """A client that raises on write is removed from the set."""
        good = _FakeStreamResponse()
        bad = _FakeStreamResponse(fail=True)
        clients: set[web.StreamResponse] = {good, bad}  # type: ignore[arg-type]
        events = [{"type": "unicode", "emoji": "😀"}]

        await _broadcast(clients, events, logging.getLogger("test"))

        assert good in clients
        assert bad not in clients
        assert len(clients) == 1

    async def test_failed_client_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Removal of failed clients is logged at DEBUG level."""
        bad = _FakeStreamResponse(fail=True)
        clients: set[web.StreamResponse] = {bad}  # type: ignore[arg-type]

        with caplog.at_level(logging.DEBUG, logger="test"):
            await _broadcast(
                clients,
                [{"type": "unicode", "emoji": "😀"}],
                logging.getLogger("test"),
            )

        assert "Removed 1 failed client(s) during broadcast" in caplog.text

    async def test_multiple_events_concatenated(self) -> None:
        """Multiple events are sent as a single concatenated payload."""
        client = _FakeStreamResponse()
        clients: set[web.StreamResponse] = {client}  # type: ignore[arg-type]
        events = [
            {"type": "unicode", "emoji": "🎉"},
            {"type": "emote", "url": "https://example.com/emote.gif"},
        ]

        await _broadcast(clients, events, logging.getLogger("test"))

        assert len(client.written) == 1
        payload = client.written[0].decode()
        assert "data: " + orjson.dumps(events[0]).decode() + "\n\n" in payload
        assert "data: " + orjson.dumps(events[1]).decode() + "\n\n" in payload

    async def test_empty_clients_noop(self) -> None:
        """Broadcasting to no clients completes without error."""
        clients: set[web.StreamResponse] = set()
        await _broadcast(
            clients,
            [{"type": "unicode", "emoji": "😀"}],
            logging.getLogger("test"),
        )
        assert len(clients) == 0

    async def test_all_clients_fail(self) -> None:
        """When every client fails, the set is emptied."""
        b1 = _FakeStreamResponse(fail=True)
        b2 = _FakeStreamResponse(fail=True)
        clients: set[web.StreamResponse] = {b1, b2}  # type: ignore[arg-type]

        await _broadcast(
            clients,
            [{"type": "unicode", "emoji": "😀"}],
            logging.getLogger("test"),
        )

        assert len(clients) == 0
