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

"""End-to-end tests for custom command placeholder processing.

Tests exercise the public API (process_placeholders) with realistic
templates and user scenarios.
"""

import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from freezegun import freeze_time

from owlbot.builtin_modules.custom_commands.placeholder_handlers import _format_duration
from owlbot.builtin_modules.custom_commands.placeholders import process_placeholders

if TYPE_CHECKING:
    from owlbot.api.storage import ModuleStorage

CREATE_COUNTERS = (
    "CREATE TABLE IF NOT EXISTS counters ("
    "name TEXT PRIMARY KEY NOT NULL, "
    "value INTEGER DEFAULT 0)"
)


@pytest.fixture
async def placeholder_storage(storage: ModuleStorage) -> ModuleStorage:
    """Storage instance with the counters table created."""
    await storage.execute(CREATE_COUNTERS)
    return storage


async def process(
    template: str,
    storage: ModuleStorage,
    args: list[str] | None = None,
    user: str = "Alice",
    use_count: int = 1,
    max_depth: int = 4,
) -> str:
    """Shorthand for process_placeholders with sensible defaults."""
    return await process_placeholders(
        template,
        args or [],
        user,
        use_count,
        storage,
        max_depth=max_depth,
    )


class TestSimpleSubstitution:
    """Basic happy-path usage of placeholders and plain text."""

    @pytest.mark.parametrize(
        "template,kwargs,expected",
        [
            pytest.param(
                "Just a plain message.", {}, "Just a plain message.", id="plain-text"
            ),
            pytest.param("", {}, "", id="empty-string"),
            pytest.param("   ", {}, "   ", id="whitespace-only"),
            pytest.param(
                "Hello, $(user)! Welcome to the stream.",
                {},
                "Hello, Alice! Welcome to the stream.",
                id="user-in-sentence",
            ),
            pytest.param(
                "The hype train has been called $(count) times! HYPE!",
                {"use_count": 7},
                "The hype train has been called 7 times! HYPE!",
                id="count-in-sentence",
            ),
            pytest.param("$(count)", {"use_count": 0}, "0", id="count-zero"),
            pytest.param(
                "Go check out $(1)! They're awesome.",
                {"args": ["Bob"]},
                "Go check out Bob! They're awesome.",
                id="arg-in-sentence",
            ),
            pytest.param("$(user)", {}, "Alice", id="user-only"),
            pytest.param(
                "$(1)$(2)$(3)",
                {"args": ["a", "b", "c"]},
                "abc",
                id="three-args-adjacent",
            ),
            pytest.param(
                "$(user) likes $(1)",
                {"args": ["cats"]},
                "Alice likes cats",
                id="user-and-arg",
            ),
            pytest.param(
                "Hello $(user)!",
                {"user": "O'Brien [MOD]"},
                "Hello O'Brien [MOD]!",
                id="special-chars-in-user",
            ),
            pytest.param(
                "$(1)",
                {"args": ["hello world & goodbye"]},
                "hello world & goodbye",
                id="special-chars-in-arg",
            ),
        ],
    )
    async def test_substitution(
        self,
        placeholder_storage: ModuleStorage,
        template: str,
        kwargs: dict[str, Any],
        expected: str,
    ) -> None:
        """Placeholders and plain text resolve to the expected string."""
        assert await process(template, placeholder_storage, **kwargs) == expected

    async def test_multiple_placeholders_in_one_template(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Multiple different placeholder types resolve in a single template."""
        await placeholder_storage.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?)", ("score", 42)
        )
        random.seed(99)
        expected_roll = random.randint(1, 6)
        random.seed(99)
        result = await process(
            "$(user) has $(getcount score) points (roll: $(rand 1 6))",
            placeholder_storage,
        )
        assert result == f"Alice has 42 points (roll: {expected_roll})"


class TestArgPlaceholders:
    """Positional arguments $(1) through $(9)."""

    @pytest.mark.parametrize(
        "template,args,expected",
        [
            pytest.param(
                "$(1)$(2)$(3)$(4)$(5)$(6)$(7)$(8)$(9)",
                ["a", "b", "c", "d", "e", "f", "g", "h", "i"],
                "abcdefghi",
                id="all-nine-args",
            ),
            pytest.param(
                "Hello $(1) and $(2)",
                ["Alice"],
                "Hello Alice and ",
                id="missing-arg-empty",
            ),
            pytest.param("[$(1)] [$(2)] [$(3)]", [], "[] [] []", id="all-args-empty"),
            pytest.param("$(1)", [""], "", id="empty-string-arg"),
            pytest.param(
                "Hello $(1)world", [""], "Hello world", id="empty-arg-adjacent-text"
            ),
        ],
    )
    async def test_arg_substitution(
        self,
        placeholder_storage: ModuleStorage,
        template: str,
        args: list[str],
        expected: str,
    ) -> None:
        """Positional arguments resolve correctly."""
        assert await process(template, placeholder_storage, args=args) == expected

    @pytest.mark.parametrize(
        "template,args,expected",
        [
            pytest.param("$(0)", ["first"], "$(0)", id="index-zero"),
            pytest.param("$(10)", ["a"] * 10, "$(10)", id="index-ten"),
            pytest.param("$(99)", [], "$(99)", id="index-ninety-nine"),
        ],
    )
    async def test_invalid_arg_index_passthrough(
        self,
        placeholder_storage: ModuleStorage,
        template: str,
        args: list[str],
        expected: str,
    ) -> None:
        """Out-of-range or unregistered numeric indices pass through unchanged."""
        result = await process(template, placeholder_storage, args=args)
        assert result == expected

    @pytest.mark.parametrize(
        "template,args,expected",
        [
            pytest.param(
                "$(1 extra)",
                ["hello"],
                "Invalid $(1): does not accept arguments",
                id="arg1-with-extra",
            ),
            pytest.param(
                "$(9 extra)",
                [],
                "Invalid $(9): does not accept arguments",
                id="arg9-with-extra",
            ),
        ],
    )
    async def test_arg_extra_args_error(
        self,
        placeholder_storage: ModuleStorage,
        template: str,
        args: list[str],
        expected: str,
    ) -> None:
        """Positional arguments with extra tokens report an error."""
        assert await process(template, placeholder_storage, args=args) == expected


class TestNamedCounters:
    """$(count name), $(getcount name), increments, resets, and deltas."""

    async def test_named_death_counter_increments(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(count name) increments the named counter by 1 each call."""
        result = await process(
            "We have died $(count deaths) times.",
            placeholder_storage,
        )
        assert result == "We have died 1 times."
        result = await process(
            "We have died $(count deaths) times.",
            placeholder_storage,
        )
        assert result == "We have died 2 times."

    async def test_getcount_without_changing(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(getcount name) reads the counter without modifying it."""
        await placeholder_storage.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?)", ("deaths", 5)
        )
        result = await process(
            "Total deaths so far: $(getcount deaths)",
            placeholder_storage,
        )
        assert result == "Total deaths so far: 5"
        row = await placeholder_storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", ("deaths",)
        )
        assert row is not None
        assert row["value"] == 5

    @pytest.mark.parametrize(
        "counter_values,template,expected,db_name,db_value",
        [
            pytest.param(
                {"deaths": 10},
                "$(count deaths 0) deaths. Counter reset!",
                "0 deaths. Counter reset!",
                "deaths",
                0,
                id="reset-to-zero",
            ),
            pytest.param({}, "$(count score 5)", "5", "score", 5, id="set-absolute"),
            pytest.param({"x": 7}, "$(count x +0)", "7", "x", 7, id="plus-zero"),
            pytest.param({"x": 7}, "$(count x -0)", "7", "x", 7, id="minus-zero"),
            pytest.param(
                {"x": 0}, "$(count x -1)", "-1", "x", -1, id="negative-result"
            ),
        ],
    )
    async def test_count_with_db_check(
        self,
        placeholder_storage: ModuleStorage,
        counter_values: dict[str, int],
        template: str,
        expected: str,
        db_name: str,
        db_value: int,
    ) -> None:
        """$(count) modifies the counter and produces the expected text."""
        for name, value in counter_values.items():
            await placeholder_storage.execute(
                "INSERT INTO counters (name, value) VALUES (?, ?)", (name, value)
            )
        result = await process(template, placeholder_storage)
        assert result == expected
        row = await placeholder_storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", (db_name,)
        )
        assert row is not None
        assert row["value"] == db_value

    @pytest.mark.parametrize(
        "counter_values,template,expected",
        [
            pytest.param({}, "$(count deaths +5)", "5", id="plus-five"),
            pytest.param(
                {"lives": 3},
                "$(count lives -1) lives remaining",
                "2 lives remaining",
                id="decrement",
            ),
            pytest.param({}, "$(getcount missing)", "0", id="getcount-missing"),
            pytest.param({}, "$(count boss_2_kills)", "1", id="underscore-in-name"),
        ],
    )
    async def test_count_simple(
        self,
        placeholder_storage: ModuleStorage,
        counter_values: dict[str, int],
        template: str,
        expected: str,
    ) -> None:
        """Named counter operations produce the expected text."""
        for name, value in counter_values.items():
            await placeholder_storage.execute(
                "INSERT INTO counters (name, value) VALUES (?, ?)", (name, value)
            )
        result = await process(template, placeholder_storage)
        assert result == expected

    async def test_multiple_named_counters(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Different named counters are independent of each other."""
        await placeholder_storage.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?)", ("wins", 3)
        )
        await placeholder_storage.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?)", ("losses", 1)
        )
        result = await process(
            "Wins: $(getcount wins) | Losses: $(getcount losses)",
            placeholder_storage,
        )
        assert result == "Wins: 3 | Losses: 1"

    async def test_adjacent_counters_both_increment(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Two different counters in one template both increment independently."""
        result = await process(
            "A: $(count a) B: $(count b)",
            placeholder_storage,
        )
        assert result == "A: 1 B: 1"
        row = await placeholder_storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", ("a",)
        )
        assert row is not None
        assert row["value"] == 1
        row = await placeholder_storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", ("b",)
        )
        assert row is not None
        assert row["value"] == 1

    async def test_same_counter_twice_in_one_template(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Two references to the same counter both increment."""
        result = await process(
            "$(count x) then $(count x)",
            placeholder_storage,
        )
        assert result == "1 then 2"

    async def test_count_and_getcount_same_counter(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(count) increments, then $(getcount) reads the new value."""
        result = await process(
            "Now: $(count pts) Total: $(getcount pts)",
            placeholder_storage,
        )
        assert result == "Now: 1 Total: 1"

    async def test_count_absolute_set_then_read(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Absolute set followed by getcount confirms persistence."""
        result = await process(
            "$(count score 42) -> $(getcount score)",
            placeholder_storage,
        )
        assert result == "42 -> 42"

    @pytest.mark.parametrize(
        "sign",
        [
            pytest.param("+", id="plus"),
            pytest.param("-", id="minus"),
        ],
    )
    async def test_bare_sign_rejected(
        self, placeholder_storage: ModuleStorage, sign: str
    ) -> None:
        """$(count x +) and $(count x -) report an error."""
        result = await process(f"$(count x {sign})", placeholder_storage)
        assert (
            result == "Invalid $(count): modifier must be an integer (e.g., +5, -1, 0)"
        )


class TestCounterNameValidation:
    """Counter names are lowercased and must match ^[a-z0-9_]+$."""

    @pytest.mark.parametrize(
        "template,expected,db_name,db_value",
        [
            pytest.param("$(count Deaths)", "1", "deaths", 1, id="uppercase-name"),
            pytest.param("$(count 123)", "1", "123", 1, id="numeric-name"),
        ],
    )
    async def test_count_name_normalized(
        self,
        placeholder_storage: ModuleStorage,
        template: str,
        expected: str,
        db_name: str,
        db_value: int,
    ) -> None:
        """Counter names are normalized and accepted."""
        result = await process(template, placeholder_storage)
        assert result == expected
        row = await placeholder_storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", (db_name,)
        )
        assert row is not None
        assert row["value"] == db_value

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("boss-kills", id="hyphen"),
            pytest.param("boss.kills", id="dot"),
            pytest.param("+5", id="leading-plus"),
            pytest.param("-3", id="leading-minus"),
        ],
    )
    async def test_count_invalid_name_rejected(
        self, placeholder_storage: ModuleStorage, name: str
    ) -> None:
        """Names with hyphens, dots, or leading +/- are rejected."""
        result = await process(f"$(count {name})", placeholder_storage)
        assert result == (
            "Invalid $(count): counter name may only contain "
            "letters, numbers, and underscores"
        )

    async def test_count_space_in_name_rejected(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """A name with a space becomes two arguments; the second is a bad modifier."""
        result = await process("$(count my counter)", placeholder_storage)
        assert (
            result == "Invalid $(count): modifier must be an integer (e.g., +5, -1, 0)"
        )

    async def test_getcount_uppercase_name_lowered(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Uppercase getcount names are normalized to lowercase."""
        await placeholder_storage.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?)", ("deaths", 5)
        )
        result = await process("$(getcount Deaths)", placeholder_storage)
        assert result == "5"

    async def test_getcount_hyphen_in_name_rejected(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Hyphens are not allowed in getcount names."""
        result = await process("$(getcount boss-kills)", placeholder_storage)
        assert result == (
            "Invalid $(getcount): counter name may only contain "
            "letters, numbers, and underscores"
        )


class TestRand:
    """All $(rand) behavior: basic usage, negative ranges, and boundaries."""

    async def test_dice_roll(self, placeholder_storage: ModuleStorage) -> None:
        """$(rand 1 6) produces a random integer in range."""
        random.seed(0)
        expected_roll = random.randint(1, 6)
        random.seed(0)
        result = await process("$(user) rolled a $(rand 1 6)!", placeholder_storage)
        assert result == f"Alice rolled a {expected_roll}!"

    @pytest.mark.parametrize(
        "template,expected",
        [
            pytest.param("$(rand 5 5)", "5", id="equal-bounds"),
            pytest.param("$(rand 0 0)", "0", id="zero-bounds"),
            pytest.param("$(rand  1  1)", "1", id="extra-whitespace"),
        ],
    )
    async def test_rand_deterministic(
        self, placeholder_storage: ModuleStorage, template: str, expected: str
    ) -> None:
        """$(rand) with fixed bounds produces a deterministic result."""
        assert await process(template, placeholder_storage) == expected

    async def test_rand_reversed_range(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(rand 10 1) auto-sorts bounds and produces a value in range."""
        random.seed(0)
        expected = random.randint(1, 10)
        random.seed(0)
        result = await process("$(rand 10 1)", placeholder_storage)
        assert result == str(expected)

    async def test_both_negative(self, placeholder_storage: ModuleStorage) -> None:
        """$(rand) works with both bounds negative."""
        random.seed(0)
        expected = random.randint(-10, -1)
        random.seed(0)
        result = await process("$(rand -10 -1)", placeholder_storage)
        assert result == str(expected)

    async def test_negative_to_positive(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(rand) works with a range crossing zero."""
        random.seed(0)
        expected = random.randint(-5, 5)
        random.seed(0)
        result = await process("$(rand -5 5)", placeholder_storage)
        assert result == str(expected)

    async def test_zero_crossing_reversed(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(rand 5 -5) auto-sorts bounds to produce a valid range."""
        random.seed(0)
        expected = random.randint(-5, 5)
        random.seed(0)
        result = await process("$(rand 5 -5)", placeholder_storage)
        assert result == str(expected)


class TestCountdownCountup:
    """Date/time placeholders and duration formatting."""

    async def test_countdown_future_date(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(countdown) with a future date returns the formatted duration."""
        with freeze_time("2026-06-15 12:00:00", tz_offset=0):
            now = datetime.now(UTC)
            # Dec 25 2099 00:00:00 EST = Dec 25 2099 05:00:00 UTC
            target = datetime(2099, 12, 25, 5, 0, 0, tzinfo=UTC)
            expected = _format_duration(int((target - now).total_seconds()))
            result = await process(
                "$(countdown Dec 25 2099 12:00:00 AM EST)", placeholder_storage
            )
            assert result == expected

    async def test_countup_past_date(self, placeholder_storage: ModuleStorage) -> None:
        """$(countup) with a past date returns the formatted elapsed duration."""
        with freeze_time("2026-06-15 12:00:00", tz_offset=0):
            now = datetime.now(UTC)
            target = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
            expected = _format_duration(int((now - target).total_seconds()))
            result = await process(
                "$(countup Jan 1 2020 12:00:00 AM UTC)", placeholder_storage
            )
            assert result == expected

    @pytest.mark.parametrize(
        "template",
        [
            pytest.param(
                "$(countdown Jan 1 2000 12:00:00 AM UTC)", id="countdown-past"
            ),
            pytest.param("$(countup Dec 25 2099 12:00:00 AM UTC)", id="countup-future"),
            pytest.param("$(countdown Jan 1 2000 12:00:00 AM utc)", id="lowercase-tz"),
        ],
    )
    async def test_zero_seconds(
        self, placeholder_storage: ModuleStorage, template: str
    ) -> None:
        """Elapsed/remaining time of zero returns '0 seconds'."""
        assert await process(template, placeholder_storage) == "0 seconds"

    @pytest.mark.parametrize(
        "placeholder,bad_date",
        [
            pytest.param("countdown", "not a real date", id="gibberish"),
            pytest.param("countdown", "monday", id="weekday-only"),
            pytest.param("countdown", "Dec 25 2099 12:00:00 AM XYZ", id="unknown-tz"),
            pytest.param("countup", "not a real date", id="countup-gibberish"),
            pytest.param("countdown", "not a valid date UTC", id="gibberish-with-tz"),
        ],
    )
    async def test_invalid_date(
        self, placeholder_storage: ModuleStorage, placeholder: str, bad_date: str
    ) -> None:
        """Placeholders with an unparseable date report an error."""
        result = await process(f"$({placeholder} {bad_date})", placeholder_storage)
        assert result == (
            f"Invalid $({placeholder}): unrecognized date format, "
            f"expected $({placeholder} Dec 25 2025 12:00:00 AM EST)"
        )

    async def test_fractional_timezone_offset(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """A fractional timezone offset like IST (UTC+5:30) is applied correctly."""
        with freeze_time("2026-06-15 12:00:00", tz_offset=0):
            now = datetime.now(UTC)
            # Jan 1 2020 12:00:00 AM IST = Dec 31 2019 18:30:00 UTC
            target = datetime(2019, 12, 31, 18, 30, 0, tzinfo=UTC)
            expected = _format_duration(int((now - target).total_seconds()))
            result = await process(
                "$(countup Jan 1 2020 12:00:00 AM IST)", placeholder_storage
            )
            assert result == expected

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            pytest.param(0, "0 seconds", id="zero"),
            pytest.param(1, "1 second", id="one-second"),
            pytest.param(2, "2 seconds", id="two-seconds"),
            pytest.param(60, "1 minute", id="one-minute"),
            pytest.param(120, "2 minutes", id="two-minutes"),
            pytest.param(3600, "1 hour", id="one-hour"),
            pytest.param(7200, "2 hours", id="two-hours"),
            pytest.param(86400, "1 day", id="one-day"),
            pytest.param(172800, "2 days", id="two-days"),
            pytest.param(3601, "1 hour 1 second", id="hour-and-second"),
            pytest.param(86401, "1 day 1 second", id="day-and-second"),
            pytest.param(86460, "1 day 1 minute", id="day-and-minute"),
            pytest.param(
                90061, "1 day 1 hour 1 minute 1 second", id="all-units-singular"
            ),
            pytest.param(
                180183, "2 days 2 hours 3 minutes 3 seconds", id="all-units-plural"
            ),
        ],
    )
    async def test_format_duration(self, seconds: int, expected: str) -> None:
        """_format_duration produces the correct human-readable string."""
        assert _format_duration(seconds) == expected


class TestNesting:
    """Nested placeholder resolution and child whitespace behavior."""

    async def test_rand_with_arg_placeholders(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(rand $(1) $(2)) resolves inner arguments before calling rand."""
        random.seed(0)
        expected = random.randint(5, 10)
        random.seed(0)
        result = await process(
            "$(rand $(1) $(2))", placeholder_storage, args=["5", "10"]
        )
        assert result == str(expected)

    async def test_multi_level_nesting(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(rand 1 $(rand 5 10)) resolves the inner rand first."""
        random.seed(0)
        inner = random.randint(5, 10)
        outer = random.randint(1, inner)
        random.seed(0)
        result = await process("$(rand 1 $(rand 5 10))", placeholder_storage)
        assert result == str(outer)

    async def test_nested_in_sentence(self, placeholder_storage: ModuleStorage) -> None:
        """Nested placeholders work when surrounded by literal text."""
        random.seed(0)
        expected_roll = random.randint(1, 20)
        random.seed(0)
        result = await process(
            "$(user) rolled $(rand $(1) $(2))!",
            placeholder_storage,
            args=["1", "20"],
        )
        assert result == f"Alice rolled {expected_roll}!"

    async def test_nested_getcount_as_rand_bound(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(getcount) result feeds into $(rand) as a bound."""
        await placeholder_storage.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?)", ("max", 50)
        )
        random.seed(0)
        expected = random.randint(1, 50)
        random.seed(0)
        result = await process("$(rand 1 $(getcount max))", placeholder_storage)
        assert result == str(expected)

    async def test_nested_count_as_rand_bound(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """A counter result feeds into rand as an argument."""
        await placeholder_storage.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?)", ("upper", 9)
        )
        # $(count upper) increments 9 -> 10.
        random.seed(0)
        expected = random.randint(1, 10)
        random.seed(0)
        result = await process(
            "$(rand 1 $(count upper))",
            placeholder_storage,
        )
        assert result == str(expected)

    async def test_three_levels_deep(self, placeholder_storage: ModuleStorage) -> None:
        """$(rand 1 $(rand 1 $(rand 5 10))) resolves inside-out."""
        random.seed(0)
        innermost = random.randint(5, 10)
        middle = random.randint(1, innermost)
        outer = random.randint(1, middle)
        random.seed(0)
        result = await process("$(rand 1 $(rand 1 $(rand 5 10)))", placeholder_storage)
        assert result == str(outer)

    async def test_multiple_nested_placeholders_in_one_template(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Two separate nested expressions in one template both resolve."""
        random.seed(0)
        low = random.randint(1, 5)
        high = random.randint(50, 100)
        random.seed(0)
        result = await process(
            "Low: $(rand $(1) $(2)) High: $(rand $(3) $(4))",
            placeholder_storage,
            args=["1", "5", "50", "100"],
        )
        assert result == f"Low: {low} High: {high}"

    async def test_nested_user_in_other_placeholder(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(user) nested inside an unknown placeholder passes through."""
        result = await process("$(greet $(user))", placeholder_storage)
        assert result == "$(greet Alice)"

    async def test_meta_nesting_degrades(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$($(1)) is not valid and degrades gracefully."""
        result = await process("$($(1))", placeholder_storage, args=["test"])
        assert result == "$(test)"

    @pytest.mark.parametrize(
        "template,args",
        [
            pytest.param("$(rand $(1) $(2))", [], id="no-args"),
            pytest.param("$(rand $(1) $(2))", ["5"], id="one-arg"),
            pytest.param("$(rand $(1)$(2))", ["5", "10"], id="no-space-between"),
        ],
    )
    async def test_nested_too_few_args(
        self, placeholder_storage: ModuleStorage, template: str, args: list[str]
    ) -> None:
        """Nested $(rand) with insufficient tokens reports too few arguments."""
        result = await process(template, placeholder_storage, args=args)
        assert (
            result == "Invalid $(rand): too few arguments, expected $(rand start stop)"
        )

    async def test_adjacent_children_with_explicit_space(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(rand $(1) $(2)) with space between children works normally."""
        result = await process(
            "$(rand $(1) $(2))", placeholder_storage, args=["3", "3"]
        )
        assert result == "3"


class TestEscaping:
    """Escape sequences and backslash boundary conditions."""

    @pytest.mark.parametrize(
        "template,kwargs,expected",
        [
            pytest.param(
                r"Use \$(user) to insert your name.",
                {},
                "Use $(user) to insert your name.",
                id="escaped-user",
            ),
            pytest.param(
                r"\$(user) and \$(count)", {}, "$(user) and $(count)", id="two-escaped"
            ),
            pytest.param(
                r"\$(user) said hello to $(user)",
                {},
                "$(user) said hello to Alice",
                id="escaped-and-live",
            ),
            pytest.param(r"path\to\file", {}, "path\\to\\file", id="backslash-in-text"),
            pytest.param("\\\\$(user)", {}, "\\$(user)", id="double-backslash"),
            pytest.param(
                "before \\$(unclosed", {}, "before \\$(unclosed", id="escaped-unclosed"
            ),
            pytest.param(
                "\\$(rand $(1) $(2))",
                {"args": ["5", "10"]},
                "$(rand $(1) $(2))",
                id="escaped-nested",
            ),
            pytest.param(r"\$()", {}, "$()", id="escaped-empty-parens"),
            pytest.param("text \\$(", {}, "text \\$(", id="trailing-escaped-dollar"),
            pytest.param(r"\$(rand 1)", {}, "$(rand 1)", id="escaped-with-body"),
            pytest.param("hello\\", {}, "hello\\", id="trailing-backslash"),
            pytest.param("\\\\\\$(user)", {}, "\\\\$(user)", id="triple-backslash"),
            pytest.param("\\$5", {}, "\\$5", id="dollar-number"),
        ],
    )
    async def test_escaping(
        self,
        placeholder_storage: ModuleStorage,
        template: str,
        kwargs: dict[str, Any],
        expected: str,
    ) -> None:
        """Escape sequences produce the expected literal text."""
        assert await process(template, placeholder_storage, **kwargs) == expected


class TestCaseInsensitivity:
    """Placeholder names are lowercased before handler lookup."""

    @pytest.mark.parametrize(
        "template,kwargs,expected",
        [
            pytest.param("$(USER)", {}, "Alice", id="all-upper-user"),
            pytest.param("$(User)", {}, "Alice", id="title-case-user"),
            pytest.param("$(Rand 1 1)", {}, "1", id="title-case-rand"),
            pytest.param("$(COUNT)", {"use_count": 5}, "5", id="all-upper-count"),
            pytest.param(
                "$(COUNTDOWN Jan 1 2000 12:00:00 AM UTC)",
                {},
                "0 seconds",
                id="all-upper-countdown",
            ),
        ],
    )
    async def test_placeholder_name_case_ignored(
        self,
        placeholder_storage: ModuleStorage,
        template: str,
        kwargs: dict[str, Any],
        expected: str,
    ) -> None:
        """Placeholder names are case-insensitive."""
        assert await process(template, placeholder_storage, **kwargs) == expected

    async def test_getcount_mixed_case(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(GetCount) resolves the same as $(getcount)."""
        await placeholder_storage.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?)", ("x", 3)
        )
        result = await process("$(GetCount x)", placeholder_storage)
        assert result == "3"

    async def test_countup_case_insensitive(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """$(COUNTUP) resolves the same as $(countup)."""
        with freeze_time("2026-06-15 12:00:00", tz_offset=0):
            now = datetime.now(UTC)
            target = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
            expected = _format_duration(int((now - target).total_seconds()))
            result = await process(
                "$(COUNTUP Jan 1 2020 12:00:00 AM UTC)", placeholder_storage
            )
            assert result == expected


class TestMaxDepth:
    """Depth limiting behavior for nested placeholders."""

    async def test_max_depth_limits_nesting(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """With max_depth=0, all $( are treated as literal text."""
        result = await process("$(user) $(rand 1 6)", placeholder_storage, max_depth=0)
        assert result == "$(user) $(rand 1 6)"

    async def test_max_depth_one_blocks_inner(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """With max_depth=1, outer placeholder works but inner $( is literal."""
        result = await process("$(user)", placeholder_storage, max_depth=1)
        assert result == "Alice"
        # Inner nesting should not resolve.
        result = await process(
            "$(rand $(1) $(2))",
            placeholder_storage,
            args=["5", "10"],
            max_depth=1,
        )
        # rand gets broken args due to literal $( and early ) close.
        assert (
            result == "Invalid $(rand): too few arguments, expected $(rand start stop)"
        )

    async def test_max_depth_two_allows_one_level(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """max_depth=2: depth-0 outer and depth-1 inner both resolve."""
        result = await process(
            "$(rand $(1) $(2))",
            placeholder_storage,
            args=["3", "3"],
            max_depth=2,
        )
        assert result == "3"

    async def test_nesting_at_default_depth_limit(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Four levels of nesting hits the default max_depth=4 boundary.

        Levels: $(rand 1 $(rand 1 $(rand 1 $(rand 5 5))))
        Depths:  0         1         2         3
        All four should resolve since max_depth=4 allows depths 0-3.
        """
        random.seed(0)
        d3 = random.randint(5, 5)  # innermost: rand 5 5
        d2 = random.randint(1, d3)
        d1 = random.randint(1, d2)
        d0 = random.randint(1, d1)
        random.seed(0)
        result = await process(
            "$(rand 1 $(rand 1 $(rand 1 $(rand 5 5))))", placeholder_storage
        )
        assert result == str(d0)

    async def test_nesting_beyond_default_depth_limit(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """Five levels of nesting exceeds default max_depth=4.

        The innermost $( at depth 4 becomes literal text, breaking the
        outer placeholders.
        """
        result = await process(
            "$(rand 1 $(rand 1 $(rand 1 $(rand 1 $(rand 5 5)))))",
            placeholder_storage,
        )
        # The depth-4 $( is literal, so the depth-3 rand gets malformed args.
        assert (
            result == "Invalid $(rand): too many arguments, expected $(rand start stop)"
        )


class TestParserBoundaries:
    """Parser edge cases around incomplete or unusual $( sequences."""

    @pytest.mark.parametrize(
        "template,expected",
        [
            pytest.param("$(rand 1", "$(rand 1", id="unclosed-rand"),
            pytest.param(
                "before $(rand 1 after", "before $(rand 1 after", id="unclosed-in-text"
            ),
            pytest.param("$()", "$()", id="empty-parens"),
            pytest.param("$", "$", id="lone-dollar"),
            pytest.param("Price: $5", "Price: $5", id="dollar-amount"),
            pytest.param("$x $y $z", "$x $y $z", id="dollar-variables"),
            pytest.param("Hello :) world", "Hello :) world", id="smiley-face"),
            pytest.param(":) :) :)", ":) :) :)", id="multiple-smileys"),
            pytest.param("hello $(", "hello $(", id="trailing-dollar-paren"),
            pytest.param("hello $(user", "hello $(user", id="unclosed-user"),
            pytest.param("$( )", "$( )", id="space-only-parens"),
            pytest.param("$(rand$(user))", "$(randAlice)", id="no-space-after-name"),
            pytest.param("$(rand$5)", "$(rand$5)", id="dollar-in-name"),
            pytest.param("$(rand$", "$(rand$", id="trailing-dollar-in-name"),
        ],
    )
    async def test_parser_boundary(
        self, placeholder_storage: ModuleStorage, template: str, expected: str
    ) -> None:
        """Parser edge cases degrade to literal text."""
        assert await process(template, placeholder_storage) == expected


class TestUnknownPlaceholders:
    """Unknown placeholder names pass through unchanged."""

    @pytest.mark.parametrize(
        "template,expected",
        [
            pytest.param("$(madeup stuff)", "$(madeup stuff)", id="with-args"),
            pytest.param("$(banana)", "$(banana)", id="bare-name"),
            pytest.param(
                "$(madeup   lots   of   space)",
                "$(madeup lots of space)",
                id="extra-spaces",
            ),
            pytest.param("$(madeup)", "$(madeup)", id="no-args"),
            pytest.param("$(café)", "$(café)", id="unicode-name"),
            pytest.param("$(café latte)", "$(café latte)", id="unicode-with-args"),
        ],
    )
    async def test_unknown_placeholder_passthrough(
        self, placeholder_storage: ModuleStorage, template: str, expected: str
    ) -> None:
        """Unrecognized placeholders pass through unchanged."""
        result = await process(template, placeholder_storage)
        assert result == expected


class TestEvaluationSafety:
    """Verify that resolved text is never re-parsed as placeholders."""

    @pytest.mark.parametrize(
        "template,kwargs,expected",
        [
            pytest.param(
                "$(1)", {"args": ["$(user)"]}, "$(user)", id="arg-contains-user"
            ),
            pytest.param(
                "$(1)",
                {"args": ["$(rand 1 100)"]},
                "$(rand 1 100)",
                id="arg-contains-rand",
            ),
            pytest.param(
                "$(user)",
                {"user": "$(rand 1 100)"},
                "$(rand 1 100)",
                id="user-contains-rand",
            ),
        ],
    )
    async def test_resolved_text_not_reparsed(
        self,
        placeholder_storage: ModuleStorage,
        template: str,
        kwargs: dict[str, Any],
        expected: str,
    ) -> None:
        """Resolved text containing placeholder syntax is not re-evaluated."""
        assert await process(template, placeholder_storage, **kwargs) == expected

    async def test_nested_arg_placeholder_syntax_not_reprocessed(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """An inner result containing $() syntax is not reprocessed."""
        result = await process(
            "$(rand $(1) $(2))", placeholder_storage, args=["5", "5"]
        )
        assert result == "5"
        # Now with args that contain placeholder syntax.
        result = await process(
            "Say: $(1)",
            placeholder_storage,
            args=["$(count) times"],
        )
        assert result == "Say: $(count) times"


class TestRuntimeErrors:
    """A PlaceholderError cancels the entire render and returns the message."""

    @pytest.mark.parametrize(
        "template,expected_error",
        [
            pytest.param(
                "$(rand 1)",
                "Invalid $(rand): too few arguments, expected $(rand start stop)",
                id="rand-too-few",
            ),
            pytest.param(
                "$(rand)",
                "Invalid $(rand): too few arguments, expected $(rand start stop)",
                id="rand-no-args",
            ),
            pytest.param(
                "$(rand 1 2 3)",
                "Invalid $(rand): too many arguments, expected $(rand start stop)",
                id="rand-too-many",
            ),
            pytest.param(
                "$(rand a b)",
                "Invalid $(rand): arguments must be integers, e.g., $(rand 1 100)",
                id="rand-non-integer",
            ),
            pytest.param(
                "$(rand 1.5 2.5)",
                "Invalid $(rand): arguments must be integers, e.g., $(rand 1 100)",
                id="rand-float-args",
            ),
            pytest.param(
                "$(user extra)",
                "Invalid $(user): does not accept arguments",
                id="user-extra-args",
            ),
            pytest.param(
                "$(getcount)",
                "Invalid $(getcount): a counter name is required",
                id="getcount-no-name",
            ),
            pytest.param(
                "$(getcount deaths extra)",
                "Invalid $(getcount): too many arguments, expected $(getcount name)",
                id="getcount-too-many",
            ),
            pytest.param(
                "$(count deaths abc)",
                "Invalid $(count): modifier must be an integer (e.g., +5, -1, 0)",
                id="count-bad-modifier",
            ),
            pytest.param(
                "$(count deaths +1 extra)",
                "Invalid $(count): too many arguments,"
                " expected $(count name [modifier])",
                id="count-too-many",
            ),
            pytest.param(
                "$(countdown)",
                "Invalid $(countdown): missing date,"
                " expected $(countdown Dec 25 2025 12:00:00 AM EST)",
                id="countdown-no-date",
            ),
            pytest.param(
                "$(countup)",
                "Invalid $(countup): missing date,"
                " expected $(countup Dec 25 2025 12:00:00 AM EST)",
                id="countup-no-date",
            ),
        ],
    )
    async def test_error_message(
        self, placeholder_storage: ModuleStorage, template: str, expected_error: str
    ) -> None:
        """Placeholder errors produce the correct diagnostic message."""
        assert await process(template, placeholder_storage) == expected_error

    async def test_error_cancels_entire_render(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """An error in any placeholder cancels the whole template."""
        result = await process("$(user) says $(rand 1)", placeholder_storage)
        assert (
            result == "Invalid $(rand): too few arguments, expected $(rand start stop)"
        )

    async def test_first_error_wins(self, placeholder_storage: ModuleStorage) -> None:
        """The first PlaceholderError encountered is returned."""
        result = await process(
            "$(rand 1) $(getcount) $(user extra)", placeholder_storage
        )
        # Evaluation is left-to-right; $(rand 1) fails first.
        assert (
            result == "Invalid $(rand): too few arguments, expected $(rand start stop)"
        )

    async def test_nested_inner_error_cancels_render(
        self, placeholder_storage: ModuleStorage
    ) -> None:
        """An error in a nested placeholder cancels the whole render."""
        result = await process("$(rand $(getcount) $(2))", placeholder_storage)
        assert result == "Invalid $(getcount): a counter name is required"
