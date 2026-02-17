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
import sqlite3
from datetime import UTC, datetime

import pytest
from freezegun import freeze_time

from owlbot.builtin_modules.custom_commands.placeholder_handlers import _format_duration
from owlbot.builtin_modules.custom_commands.placeholders import process_placeholders


class MockStorage:
    """In-memory SQLite storage that executes real SQL queries."""

    def __init__(self, counter_values=None):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE counters ("
            "name TEXT PRIMARY KEY NOT NULL, "
            "value INTEGER DEFAULT 0)"
        )
        for name, value in (counter_values or {}).items():
            self._conn.execute(
                "INSERT INTO counters (name, value) VALUES (?, ?)",
                (name, value),
            )
        self._conn.commit()

    async def fetch_one(self, query, params=None):
        cursor = self._conn.execute(query, params or ())
        row = cursor.fetchone()
        self._conn.commit()
        return row

    async def fetch_value(self, query, params=None):
        row = await self.fetch_one(query, params)
        if row is None:
            return None
        return row[0]


async def process(
    template, args=None, user="Alice", use_count=1, storage=None, max_depth=4
):
    """Shorthand for process_placeholders with sensible defaults."""
    return await process_placeholders(
        template,
        args or [],
        user,
        use_count,
        storage or MockStorage(),
        max_depth=max_depth,
    )


class TestSimpleSubstitution:
    """Basic happy-path usage of placeholders and plain text."""

    @pytest.mark.parametrize(
        "template,kwargs,expected",
        [
            ("Just a plain message.", {}, "Just a plain message."),
            ("", {}, ""),
            ("   ", {}, "   "),
            (
                "Hello, $(user)! Welcome to the stream.",
                {},
                "Hello, Alice! Welcome to the stream.",
            ),
            (
                "The hype train has been called $(count) times! HYPE!",
                {"use_count": 7},
                "The hype train has been called 7 times! HYPE!",
            ),
            ("$(count)", {"use_count": 0}, "0"),
            (
                "Go check out $(1)! They're awesome.",
                {"args": ["Bob"]},
                "Go check out Bob! They're awesome.",
            ),
            ("$(user)", {}, "Alice"),
            ("$(1)$(2)$(3)", {"args": ["a", "b", "c"]}, "abc"),
            ("$(user) likes $(1)", {"args": ["cats"]}, "Alice likes cats"),
            ("Hello $(user)!", {"user": "O'Brien [MOD]"}, "Hello O'Brien [MOD]!"),
            ("$(1)", {"args": ["hello world & goodbye"]}, "hello world & goodbye"),
        ],
    )
    async def test_substitution(self, template, kwargs, expected):
        """Placeholders and plain text resolve to the expected string."""
        assert await process(template, **kwargs) == expected

    async def test_multiple_placeholders_in_one_template(self):
        """Multiple different placeholder types resolve in a single template."""
        storage = MockStorage({"score": 42})
        random.seed(99)
        expected_roll = random.randint(1, 6)
        random.seed(99)
        result = await process(
            "$(user) has $(getcount score) points (roll: $(rand 1 6))",
            storage=storage,
        )
        assert result == f"Alice has 42 points (roll: {expected_roll})"


class TestArgPlaceholders:
    """Positional arguments $(1) through $(9)."""

    @pytest.mark.parametrize(
        "template,args,expected",
        [
            (
                "$(1)$(2)$(3)$(4)$(5)$(6)$(7)$(8)$(9)",
                ["a", "b", "c", "d", "e", "f", "g", "h", "i"],
                "abcdefghi",
            ),
            ("Hello $(1) and $(2)", ["Alice"], "Hello Alice and "),
            ("[$(1)] [$(2)] [$(3)]", [], "[] [] []"),
            ("$(1)", [""], ""),
            ("Hello $(1)world", [""], "Hello world"),
        ],
    )
    async def test_arg_substitution(self, template, args, expected):
        """Positional arguments resolve correctly."""
        assert await process(template, args=args) == expected

    @pytest.mark.parametrize(
        "template,args,expected",
        [
            ("$(0)", ["first"], "$(0)"),
            ("$(10)", ["a"] * 10, "$(10)"),
            ("$(99)", [], "$(99)"),
        ],
    )
    async def test_invalid_arg_index_passthrough(self, template, args, expected):
        """Out-of-range or unregistered numeric indices pass through unchanged."""
        result = await process(template, args=args)
        assert result == expected

    @pytest.mark.parametrize(
        "template,args,expected",
        [
            ("$(1 extra)", ["hello"], "Invalid $(1): does not accept arguments"),
            ("$(9 extra)", [], "Invalid $(9): does not accept arguments"),
        ],
    )
    async def test_arg_extra_args_error(self, template, args, expected):
        """Positional arguments with extra tokens report an error."""
        assert await process(template, args=args) == expected


class TestNamedCounters:
    """$(count name), $(getcount name), increments, resets, and deltas."""

    async def test_named_death_counter_increments(self):
        """$(count name) increments the named counter by 1 each call."""
        storage = MockStorage()
        result = await process(
            "We have died $(count deaths) times.",
            storage=storage,
        )
        assert result == "We have died 1 times."
        result = await process(
            "We have died $(count deaths) times.",
            storage=storage,
        )
        assert result == "We have died 2 times."

    async def test_getcount_without_changing(self):
        """$(getcount name) reads the counter without modifying it."""
        storage = MockStorage({"deaths": 5})
        result = await process(
            "Total deaths so far: $(getcount deaths)",
            storage=storage,
        )
        assert result == "Total deaths so far: 5"
        row = await storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", ("deaths",)
        )
        assert row["value"] == 5

    @pytest.mark.parametrize(
        "counter_values,template,expected,db_name,db_value",
        [
            (
                {"deaths": 10},
                "$(count deaths 0) deaths. Counter reset!",
                "0 deaths. Counter reset!",
                "deaths",
                0,
            ),
            ({}, "$(count score 5)", "5", "score", 5),
            ({"x": 7}, "$(count x +0)", "7", "x", 7),
            ({"x": 7}, "$(count x -0)", "7", "x", 7),
            ({"x": 0}, "$(count x -1)", "-1", "x", -1),
        ],
    )
    async def test_count_with_db_check(
        self, counter_values, template, expected, db_name, db_value
    ):
        """$(count) modifies the counter and produces the expected text."""
        storage = MockStorage(counter_values)
        result = await process(template, storage=storage)
        assert result == expected
        row = await storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", (db_name,)
        )
        assert row["value"] == db_value

    @pytest.mark.parametrize(
        "counter_values,template,expected",
        [
            ({}, "$(count deaths +5)", "5"),
            ({"lives": 3}, "$(count lives -1) lives remaining", "2 lives remaining"),
            ({}, "$(getcount missing)", "0"),
            ({}, "$(count boss_2_kills)", "1"),
        ],
    )
    async def test_count_simple(self, counter_values, template, expected):
        """Named counter operations produce the expected text."""
        storage = MockStorage(counter_values)
        result = await process(template, storage=storage)
        assert result == expected

    async def test_multiple_named_counters(self):
        """Different named counters are independent of each other."""
        storage = MockStorage({"wins": 3, "losses": 1})
        result = await process(
            "Wins: $(getcount wins) | Losses: $(getcount losses)",
            storage=storage,
        )
        assert result == "Wins: 3 | Losses: 1"

    async def test_adjacent_counters_both_increment(self):
        """Two different counters in one template both increment independently."""
        storage = MockStorage()
        result = await process(
            "A: $(count a) B: $(count b)",
            storage=storage,
        )
        assert result == "A: 1 B: 1"
        row = await storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", ("a",)
        )
        assert row["value"] == 1
        row = await storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", ("b",)
        )
        assert row["value"] == 1

    async def test_same_counter_twice_in_one_template(self):
        """Two references to the same counter both increment."""
        storage = MockStorage()
        result = await process(
            "$(count x) then $(count x)",
            storage=storage,
        )
        assert result == "1 then 2"

    async def test_count_and_getcount_same_counter(self):
        """$(count) increments, then $(getcount) reads the new value."""
        storage = MockStorage()
        result = await process(
            "Now: $(count pts) Total: $(getcount pts)",
            storage=storage,
        )
        assert result == "Now: 1 Total: 1"

    async def test_count_absolute_set_then_read(self):
        """Absolute set followed by getcount confirms persistence."""
        storage = MockStorage()
        result = await process(
            "$(count score 42) -> $(getcount score)",
            storage=storage,
        )
        assert result == "42 -> 42"

    @pytest.mark.parametrize("sign", ["+", "-"])
    async def test_bare_sign_rejected(self, sign):
        """$(count x +) and $(count x -) report an error."""
        result = await process(f"$(count x {sign})")
        assert (
            result == "Invalid $(count): modifier must be an integer (e.g., +5, -1, 0)"
        )


class TestCounterNameValidation:
    """Counter names are lowercased and must match ^[a-z0-9_]+$."""

    @pytest.mark.parametrize(
        "template,expected,db_name,db_value",
        [
            ("$(count Deaths)", "1", "deaths", 1),
            ("$(count 123)", "1", "123", 1),
        ],
    )
    async def test_count_name_normalized(self, template, expected, db_name, db_value):
        """Counter names are normalized and accepted."""
        storage = MockStorage()
        result = await process(template, storage=storage)
        assert result == expected
        row = await storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", (db_name,)
        )
        assert row["value"] == db_value

    @pytest.mark.parametrize("name", ["boss-kills", "boss.kills", "+5", "-3"])
    async def test_count_invalid_name_rejected(self, name):
        """Names with hyphens, dots, or leading +/- are rejected."""
        result = await process(f"$(count {name})")
        assert result == (
            "Invalid $(count): counter name may only contain "
            "letters, numbers, and underscores"
        )

    async def test_count_space_in_name_rejected(self):
        """A name with a space becomes two arguments; the second is a bad modifier."""
        result = await process("$(count my counter)")
        assert (
            result == "Invalid $(count): modifier must be an integer (e.g., +5, -1, 0)"
        )

    async def test_getcount_uppercase_name_lowered(self):
        """Uppercase getcount names are normalized to lowercase."""
        storage = MockStorage({"deaths": 5})
        result = await process("$(getcount Deaths)", storage=storage)
        assert result == "5"

    async def test_getcount_hyphen_in_name_rejected(self):
        """Hyphens are not allowed in getcount names."""
        result = await process("$(getcount boss-kills)")
        assert result == (
            "Invalid $(getcount): counter name may only contain "
            "letters, numbers, and underscores"
        )


class TestRand:
    """All $(rand) behavior: basic usage, negative ranges, and boundaries."""

    async def test_dice_roll(self):
        """$(rand 1 6) produces a random integer in range."""
        random.seed(0)
        expected_roll = random.randint(1, 6)
        random.seed(0)
        result = await process("$(user) rolled a $(rand 1 6)!")
        assert result == f"Alice rolled a {expected_roll}!"

    @pytest.mark.parametrize(
        "template,expected",
        [
            ("$(rand 5 5)", "5"),
            ("$(rand 0 0)", "0"),
            ("$(rand  1  1)", "1"),
        ],
    )
    async def test_rand_deterministic(self, template, expected):
        """$(rand) with fixed bounds produces a deterministic result."""
        assert await process(template) == expected

    async def test_rand_reversed_range(self):
        """$(rand 10 1) auto-sorts bounds and produces a value in range."""
        random.seed(0)
        expected = random.randint(1, 10)
        random.seed(0)
        result = await process("$(rand 10 1)")
        assert result == str(expected)

    async def test_both_negative(self):
        """$(rand) works with both bounds negative."""
        random.seed(0)
        expected = random.randint(-10, -1)
        random.seed(0)
        result = await process("$(rand -10 -1)")
        assert result == str(expected)

    async def test_negative_to_positive(self):
        """$(rand) works with a range crossing zero."""
        random.seed(0)
        expected = random.randint(-5, 5)
        random.seed(0)
        result = await process("$(rand -5 5)")
        assert result == str(expected)

    async def test_zero_crossing_reversed(self):
        """$(rand 5 -5) auto-sorts bounds to produce a valid range."""
        random.seed(0)
        expected = random.randint(-5, 5)
        random.seed(0)
        result = await process("$(rand 5 -5)")
        assert result == str(expected)


class TestCountdownCountup:
    """Date/time placeholders and duration formatting."""

    async def test_countdown_future_date(self):
        """$(countdown) with a future date returns the formatted duration."""
        with freeze_time("2026-06-15 12:00:00", tz_offset=0):
            now = datetime.now(UTC)
            # Dec 25 2099 00:00:00 EST = Dec 25 2099 05:00:00 UTC
            target = datetime(2099, 12, 25, 5, 0, 0, tzinfo=UTC)
            expected = _format_duration(int((target - now).total_seconds()))
            result = await process("$(countdown Dec 25 2099 12:00:00 AM EST)")
            assert result == expected

    async def test_countup_past_date(self):
        """$(countup) with a past date returns the formatted elapsed duration."""
        with freeze_time("2026-06-15 12:00:00", tz_offset=0):
            now = datetime.now(UTC)
            target = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
            expected = _format_duration(int((now - target).total_seconds()))
            result = await process("$(countup Jan 1 2020 12:00:00 AM UTC)")
            assert result == expected

    @pytest.mark.parametrize(
        "template",
        [
            "$(countdown Jan 1 2000 12:00:00 AM UTC)",
            "$(countup Dec 25 2099 12:00:00 AM UTC)",
            "$(countdown Jan 1 2000 12:00:00 AM utc)",
        ],
    )
    async def test_zero_seconds(self, template):
        """Elapsed/remaining time of zero returns '0 seconds'."""
        assert await process(template) == "0 seconds"

    @pytest.mark.parametrize(
        "placeholder,bad_date",
        [
            ("countdown", "not a real date"),
            ("countdown", "monday"),
            ("countdown", "Dec 25 2099 12:00:00 AM XYZ"),
            ("countup", "not a real date"),
            ("countdown", "not a valid date UTC"),
        ],
    )
    async def test_invalid_date(self, placeholder, bad_date):
        """Placeholders with an unparseable date report an error."""
        result = await process(f"$({placeholder} {bad_date})")
        assert result == (
            f"Invalid $({placeholder}): unrecognized date format, "
            f"expected $({placeholder} Dec 25 2025 12:00:00 AM EST)"
        )

    async def test_fractional_timezone_offset(self):
        """A fractional timezone offset like IST (UTC+5:30) is applied correctly."""
        with freeze_time("2026-06-15 12:00:00", tz_offset=0):
            now = datetime.now(UTC)
            # Jan 1 2020 12:00:00 AM IST = Dec 31 2019 18:30:00 UTC
            target = datetime(2019, 12, 31, 18, 30, 0, tzinfo=UTC)
            expected = _format_duration(int((now - target).total_seconds()))
            result = await process("$(countup Jan 1 2020 12:00:00 AM IST)")
            assert result == expected

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "0 seconds"),
            (1, "1 second"),
            (2, "2 seconds"),
            (60, "1 minute"),
            (120, "2 minutes"),
            (3600, "1 hour"),
            (7200, "2 hours"),
            (86400, "1 day"),
            (172800, "2 days"),
            (3601, "1 hour 1 second"),
            (86401, "1 day 1 second"),
            (86460, "1 day 1 minute"),
            (90061, "1 day 1 hour 1 minute 1 second"),
            (180183, "2 days 2 hours 3 minutes 3 seconds"),
        ],
    )
    async def test_format_duration(self, seconds, expected):
        """_format_duration produces the correct human-readable string."""
        assert _format_duration(seconds) == expected


class TestNesting:
    """Nested placeholder resolution and child whitespace behavior."""

    async def test_rand_with_arg_placeholders(self):
        """$(rand $(1) $(2)) resolves inner arguments before calling rand."""
        random.seed(0)
        expected = random.randint(5, 10)
        random.seed(0)
        result = await process("$(rand $(1) $(2))", args=["5", "10"])
        assert result == str(expected)

    async def test_multi_level_nesting(self):
        """$(rand 1 $(rand 5 10)) resolves the inner rand first."""
        random.seed(0)
        inner = random.randint(5, 10)
        outer = random.randint(1, inner)
        random.seed(0)
        result = await process("$(rand 1 $(rand 5 10))")
        assert result == str(outer)

    async def test_nested_in_sentence(self):
        """Nested placeholders work when surrounded by literal text."""
        random.seed(0)
        expected_roll = random.randint(1, 20)
        random.seed(0)
        result = await process(
            "$(user) rolled $(rand $(1) $(2))!",
            args=["1", "20"],
        )
        assert result == f"Alice rolled {expected_roll}!"

    async def test_nested_getcount_as_rand_bound(self):
        """$(getcount) result feeds into $(rand) as a bound."""
        storage = MockStorage({"max": 50})
        random.seed(0)
        expected = random.randint(1, 50)
        random.seed(0)
        result = await process("$(rand 1 $(getcount max))", storage=storage)
        assert result == str(expected)

    async def test_nested_count_as_rand_bound(self):
        """A counter result feeds into rand as an argument."""
        storage = MockStorage({"upper": 9})
        # $(count upper) increments 9 -> 10.
        random.seed(0)
        expected = random.randint(1, 10)
        random.seed(0)
        result = await process(
            "$(rand 1 $(count upper))",
            storage=storage,
        )
        assert result == str(expected)

    async def test_three_levels_deep(self):
        """$(rand 1 $(rand 1 $(rand 5 10))) resolves inside-out."""
        random.seed(0)
        innermost = random.randint(5, 10)
        middle = random.randint(1, innermost)
        outer = random.randint(1, middle)
        random.seed(0)
        result = await process("$(rand 1 $(rand 1 $(rand 5 10)))")
        assert result == str(outer)

    async def test_multiple_nested_placeholders_in_one_template(self):
        """Two separate nested expressions in one template both resolve."""
        random.seed(0)
        low = random.randint(1, 5)
        high = random.randint(50, 100)
        random.seed(0)
        result = await process(
            "Low: $(rand $(1) $(2)) High: $(rand $(3) $(4))",
            args=["1", "5", "50", "100"],
        )
        assert result == f"Low: {low} High: {high}"

    async def test_nested_user_in_other_placeholder(self):
        """$(user) nested inside an unknown placeholder passes through."""
        result = await process("$(greet $(user))")
        assert result == "$(greet Alice)"

    async def test_meta_nesting_degrades(self):
        """$($(1)) is not valid and degrades gracefully."""
        result = await process("$($(1))", args=["test"])
        assert result == "$(test)"

    @pytest.mark.parametrize(
        "template,args",
        [
            ("$(rand $(1) $(2))", []),
            ("$(rand $(1) $(2))", ["5"]),
            ("$(rand $(1)$(2))", ["5", "10"]),
        ],
    )
    async def test_nested_too_few_args(self, template, args):
        """Nested $(rand) with insufficient tokens reports too few arguments."""
        result = await process(template, args=args)
        assert (
            result == "Invalid $(rand): too few arguments, expected $(rand start stop)"
        )

    async def test_adjacent_children_with_explicit_space(self):
        """$(rand $(1) $(2)) with space between children works normally."""
        result = await process("$(rand $(1) $(2))", args=["3", "3"])
        assert result == "3"


class TestEscaping:
    """Escape sequences and backslash boundary conditions."""

    @pytest.mark.parametrize(
        "template,kwargs,expected",
        [
            (
                r"Use \$(user) to insert your name.",
                {},
                "Use $(user) to insert your name.",
            ),
            (r"\$(user) and \$(count)", {}, "$(user) and $(count)"),
            (r"\$(user) said hello to $(user)", {}, "$(user) said hello to Alice"),
            (r"path\to\file", {}, "path\\to\\file"),
            ("\\\\$(user)", {}, "\\$(user)"),
            ("before \\$(unclosed", {}, "before \\$(unclosed"),
            ("\\$(rand $(1) $(2))", {"args": ["5", "10"]}, "$(rand $(1) $(2))"),
            (r"\$()", {}, "$()"),
            ("text \\$(", {}, "text \\$("),
            (r"\$(rand 1)", {}, "$(rand 1)"),
            ("hello\\", {}, "hello\\"),
            ("\\\\\\$(user)", {}, "\\\\$(user)"),
            ("\\$5", {}, "\\$5"),
        ],
    )
    async def test_escaping(self, template, kwargs, expected):
        """Escape sequences produce the expected literal text."""
        assert await process(template, **kwargs) == expected


class TestCaseInsensitivity:
    """Placeholder names are lowercased before handler lookup."""

    @pytest.mark.parametrize(
        "template,kwargs,expected",
        [
            ("$(USER)", {}, "Alice"),
            ("$(User)", {}, "Alice"),
            ("$(Rand 1 1)", {}, "1"),
            ("$(COUNT)", {"use_count": 5}, "5"),
            ("$(COUNTDOWN Jan 1 2000 12:00:00 AM UTC)", {}, "0 seconds"),
        ],
    )
    async def test_placeholder_name_case_ignored(self, template, kwargs, expected):
        """Placeholder names are case-insensitive."""
        assert await process(template, **kwargs) == expected

    async def test_getcount_mixed_case(self):
        """$(GetCount) resolves the same as $(getcount)."""
        storage = MockStorage({"x": 3})
        result = await process("$(GetCount x)", storage=storage)
        assert result == "3"

    async def test_countup_case_insensitive(self):
        """$(COUNTUP) resolves the same as $(countup)."""
        with freeze_time("2026-06-15 12:00:00", tz_offset=0):
            now = datetime.now(UTC)
            target = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
            expected = _format_duration(int((now - target).total_seconds()))
            result = await process("$(COUNTUP Jan 1 2020 12:00:00 AM UTC)")
            assert result == expected


class TestMaxDepth:
    """Depth limiting behavior for nested placeholders."""

    async def test_max_depth_limits_nesting(self):
        """With max_depth=0, all $( are treated as literal text."""
        result = await process("$(user) $(rand 1 6)", max_depth=0)
        assert result == "$(user) $(rand 1 6)"

    async def test_max_depth_one_blocks_inner(self):
        """With max_depth=1, outer placeholder works but inner $( is literal."""
        result = await process("$(user)", max_depth=1)
        assert result == "Alice"
        # Inner nesting should not resolve.
        result = await process(
            "$(rand $(1) $(2))",
            args=["5", "10"],
            max_depth=1,
        )
        # rand gets broken args due to literal $( and early ) close.
        assert (
            result == "Invalid $(rand): too few arguments, expected $(rand start stop)"
        )

    async def test_max_depth_two_allows_one_level(self):
        """max_depth=2: depth-0 outer and depth-1 inner both resolve."""
        result = await process(
            "$(rand $(1) $(2))",
            args=["3", "3"],
            max_depth=2,
        )
        assert result == "3"

    async def test_nesting_at_default_depth_limit(self):
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
        result = await process("$(rand 1 $(rand 1 $(rand 1 $(rand 5 5))))")
        assert result == str(d0)

    async def test_nesting_beyond_default_depth_limit(self):
        """Five levels of nesting exceeds default max_depth=4.

        The innermost $( at depth 4 becomes literal text, breaking the
        outer placeholders.
        """
        result = await process("$(rand 1 $(rand 1 $(rand 1 $(rand 1 $(rand 5 5)))))")
        # The depth-4 $( is literal, so the depth-3 rand gets malformed args.
        assert (
            result == "Invalid $(rand): too many arguments, expected $(rand start stop)"
        )


class TestParserBoundaries:
    """Parser edge cases around incomplete or unusual $( sequences."""

    @pytest.mark.parametrize(
        "template,expected",
        [
            ("$(rand 1", "$(rand 1"),
            ("before $(rand 1 after", "before $(rand 1 after"),
            ("$()", "$()"),
            ("$", "$"),
            ("Price: $5", "Price: $5"),
            ("$x $y $z", "$x $y $z"),
            ("Hello :) world", "Hello :) world"),
            (":) :) :)", ":) :) :)"),
            ("hello $(", "hello $("),
            ("hello $(user", "hello $(user"),
            ("$( )", "$( )"),
            ("$(rand$(user))", "$(randAlice)"),
            ("$(rand$5)", "$(rand$5)"),
            ("$(rand$", "$(rand$"),
        ],
    )
    async def test_parser_boundary(self, template, expected):
        """Parser edge cases degrade to literal text."""
        assert await process(template) == expected


class TestUnknownPlaceholders:
    """Unknown placeholder names pass through unchanged."""

    @pytest.mark.parametrize(
        "template,expected",
        [
            ("$(madeup stuff)", "$(madeup stuff)"),
            ("$(banana)", "$(banana)"),
            ("$(madeup   lots   of   space)", "$(madeup lots of space)"),
            ("$(madeup)", "$(madeup)"),
            ("$(café)", "$(café)"),
            ("$(café latte)", "$(café latte)"),
        ],
    )
    async def test_unknown_placeholder_passthrough(self, template, expected):
        """Unrecognized placeholders pass through unchanged."""
        result = await process(template)
        assert result == expected


class TestEvaluationSafety:
    """Verify that resolved text is never re-parsed as placeholders."""

    @pytest.mark.parametrize(
        "template,kwargs,expected",
        [
            ("$(1)", {"args": ["$(user)"]}, "$(user)"),
            ("$(1)", {"args": ["$(rand 1 100)"]}, "$(rand 1 100)"),
            ("$(user)", {"user": "$(rand 1 100)"}, "$(rand 1 100)"),
        ],
    )
    async def test_resolved_text_not_reparsed(self, template, kwargs, expected):
        """Resolved text containing placeholder syntax is not re-evaluated."""
        assert await process(template, **kwargs) == expected

    async def test_nested_arg_placeholder_syntax_not_reprocessed(self):
        """An inner result containing $() syntax is not reprocessed."""
        result = await process("$(rand $(1) $(2))", args=["5", "5"])
        assert result == "5"
        # Now with args that contain placeholder syntax.
        result = await process(
            "Say: $(1)",
            args=["$(count) times"],
        )
        assert result == "Say: $(count) times"


class TestRuntimeErrors:
    """A PlaceholderError cancels the entire render and returns the message."""

    @pytest.mark.parametrize(
        "template,expected_error",
        [
            (
                "$(rand 1)",
                "Invalid $(rand): too few arguments, expected $(rand start stop)",
            ),
            (
                "$(rand)",
                "Invalid $(rand): too few arguments, expected $(rand start stop)",
            ),
            (
                "$(rand 1 2 3)",
                "Invalid $(rand): too many arguments, expected $(rand start stop)",
            ),
            (
                "$(rand a b)",
                "Invalid $(rand): arguments must be integers, e.g., $(rand 1 100)",
            ),
            (
                "$(rand 1.5 2.5)",
                "Invalid $(rand): arguments must be integers, e.g., $(rand 1 100)",
            ),
            ("$(user extra)", "Invalid $(user): does not accept arguments"),
            ("$(getcount)", "Invalid $(getcount): a counter name is required"),
            (
                "$(getcount deaths extra)",
                "Invalid $(getcount): too many arguments, expected $(getcount name)",
            ),
            (
                "$(count deaths abc)",
                "Invalid $(count): modifier must be an integer (e.g., +5, -1, 0)",
            ),
            (
                "$(count deaths +1 extra)",
                "Invalid $(count): too many arguments,"
                " expected $(count name [modifier])",
            ),
            (
                "$(countdown)",
                "Invalid $(countdown): missing date,"
                " expected $(countdown Dec 25 2025 12:00:00 AM EST)",
            ),
            (
                "$(countup)",
                "Invalid $(countup): missing date,"
                " expected $(countup Dec 25 2025 12:00:00 AM EST)",
            ),
        ],
    )
    async def test_error_message(self, template, expected_error):
        """Placeholder errors produce the correct diagnostic message."""
        assert await process(template) == expected_error

    async def test_error_cancels_entire_render(self):
        """An error in any placeholder cancels the whole template."""
        result = await process("$(user) says $(rand 1)")
        assert (
            result == "Invalid $(rand): too few arguments, expected $(rand start stop)"
        )

    async def test_first_error_wins(self):
        """The first PlaceholderError encountered is returned."""
        result = await process("$(rand 1) $(getcount) $(user extra)")
        # Evaluation is left-to-right; $(rand 1) fails first.
        assert (
            result == "Invalid $(rand): too few arguments, expected $(rand start stop)"
        )

    async def test_nested_inner_error_cancels_render(self):
        """An error in a nested placeholder cancels the whole render."""
        result = await process("$(rand $(getcount) $(2))")
        assert result == "Invalid $(getcount): a counter name is required"
