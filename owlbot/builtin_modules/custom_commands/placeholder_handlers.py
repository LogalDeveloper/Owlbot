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

"""Handler functions for placeholder resolution.

Each handler is a plain async function that receives the placeholder name,
pre-resolved argument list, and a :class:`PlaceholderContext`, and returns the
replacement string.  Handlers raise :exc:`PlaceholderError` to report bad
arguments; the engine immediately stops processing and returns ``str(e)`` as
the entire response, discarding any other template content.
"""

import random
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .placeholders import PlaceholderContext


class PlaceholderError(Exception):
    """Raised by a placeholder handler to report a resolution error."""


# Sourced from the Wikipedia "List of time zone abbreviations" article, which
# compiles data from the IANA Time Zone Database and other references.
# https://en.wikipedia.org/wiki/List_of_time_zone_abbreviations
#
# Keys are uppercase because the lookup normalises user input with .upper().
# Ambiguous abbreviations use the most common interpretation:
#   ACT = Acre Time (-5), not ASEAN Common Time (+8)
#   AMT = Amazon Time (-4), not Armenia Time (+4)
#   AST = Atlantic Standard Time (-4), not Arabia Standard Time (+3)
#   BST = British Summer Time (+1), not Bangladesh/Bougainville
#   CDT = Central Daylight Time (-5), not Cuba Daylight Time (-4)
#   CST = Central Standard Time (-6), not China (+8) or Cuba (-5)
#   ECT = Ecuador Time (-5), not Eastern Caribbean Time (-4)
#   GST = Gulf Standard Time (+4), not South Georgia Time (-2)
#   IST = India Standard Time (+5:30), not Irish (+1) or Israel (+2)
#   LHST = Lord Howe Standard Time (+10:30), not summer (+11)
#   MST = Mountain Standard Time (-7), not Malaysia Standard Time (+8)
#   PST = Pacific Standard Time (-8), not Philippine Standard Time (+8)
TIMEZONE_OFFSETS: dict[str, float] = {
    "ACDT": 10.5,
    "ACST": 9.5,
    "ACT": -5,
    "ACWST": 8.75,
    "ADT": -3,
    "AEDT": 11,
    "AEST": 10,
    "AFT": 4.5,
    "AKDT": -8,
    "AKST": -9,
    "ALMT": 6,
    "AMST": -3,
    "AMT": -4,
    "ANAT": 12,
    "AQTT": 5,
    "ART": -3,
    "AST": -4,
    "AWST": 8,
    "AZOST": 0,
    "AZOT": -1,
    "AZT": 4,
    "BIT": -12,
    "BIOT": 6,
    "BNT": 8,
    "BOT": -4,
    "BRST": -2,
    "BRT": -3,
    "BST": 1,
    "BTT": 6,
    "CAT": 2,
    "CCT": 6.5,
    "CDT": -5,
    "CEST": 2,
    "CET": 1,
    "CHADT": 13.75,
    "CHAST": 12.75,
    "CHOT": 8,
    "CHOST": 9,
    "CHST": 10,
    "CHUT": 10,
    "CIST": -8,
    "CKT": -10,
    "CLST": -3,
    "CLT": -4,
    "COST": -4,
    "COT": -5,
    "CST": -6,
    "CVT": -1,
    "CWST": 8.75,
    "CXT": 7,
    "DAVT": 7,
    "DDUT": 10,
    "DFT": 1,
    "EASST": -5,
    "EAST": -6,
    "EAT": 3,
    "ECT": -5,
    "EDT": -4,
    "EEST": 3,
    "EET": 2,
    "EGST": 0,
    "EGT": -1,
    "EST": -5,
    "FET": 3,
    "FJT": 12,
    "FKST": -3,
    "FKT": -4,
    "FNT": -2,
    "GALT": -6,
    "GAMT": -9,
    "GET": 4,
    "GFT": -3,
    "GILT": 12,
    "GIT": -9,
    "GMT": 0,
    "GST": 4,
    "GYT": -4,
    "HAEC": 2,
    "HDT": -9,
    "HKT": 8,
    "HMT": 5,
    "HOVST": 8,
    "HOVT": 7,
    "HST": -10,
    "ICT": 7,
    "IDLW": -12,
    "IDT": 3,
    "IOT": 6,
    "IRDT": 4.5,
    "IRKT": 8,
    "IRST": 3.5,
    "IST": 5.5,
    "JST": 9,
    "KALT": 2,
    "KGT": 6,
    "KOST": 11,
    "KRAT": 7,
    "KST": 9,
    "LHST": 10.5,
    "LINT": 14,
    "MAGT": 12,
    "MART": -9.5,
    "MAWT": 5,
    "MDT": -6,
    "MEST": 2,
    "MET": 1,
    "MHT": 12,
    "MIST": 11,
    "MIT": -9.5,
    "MMT": 6.5,
    "MSK": 3,
    "MST": -7,
    "MUT": 4,
    "MVT": 5,
    "MYT": 8,
    "NCT": 11,
    "NDT": -2.5,
    "NFT": 11,
    "NOVT": 7,
    "NPT": 5.75,
    "NST": -3.5,
    "NT": -3.5,
    "NUT": -11,
    "NZDT": 13,
    "NZDST": 13,
    "NZST": 12,
    "OMST": 6,
    "ORAT": 5,
    "PDT": -7,
    "PET": -5,
    "PETT": 12,
    "PGT": 10,
    "PHOT": 13,
    "PHST": 8,
    "PHT": 8,
    "PKT": 5,
    "PMDT": -2,
    "PMST": -3,
    "PONT": 11,
    "PST": -8,
    "PWT": 9,
    "PYST": -3,
    "PYT": -4,
    "RET": 4,
    "ROTT": -3,
    "SAKT": 11,
    "SAMT": 4,
    "SAST": 2,
    "SBT": 11,
    "SCT": 4,
    "SDT": -10,
    "SGT": 8,
    "SLST": 5.5,
    "SRET": 11,
    "SRT": -3,
    "SST": -11,
    "SYOT": 3,
    "TAHT": -10,
    "TFT": 5,
    "THA": 7,
    "TJT": 5,
    "TKT": 13,
    "TLT": 9,
    "TMT": 5,
    "TOT": 13,
    "TRT": 3,
    "TST": 8,
    "TVT": 12,
    "ULAST": 9,
    "ULAT": 8,
    "UTC": 0,
    "UYST": -2,
    "UYT": -3,
    "UZT": 5,
    "VET": -4,
    "VLAT": 10,
    "VOLT": 3,
    "VOST": 6,
    "VUT": 11,
    "WAKT": 12,
    "WAST": 2,
    "WAT": 1,
    "WEST": 1,
    "WET": 0,
    "WGST": -2,
    "WGT": -3,
    "WIB": 7,
    "WIT": 9,
    "WITA": 8,
    "WST": 8,
    "YAKT": 9,
    "YEKT": 5,
}

# Pattern for validating counter names.
_COUNTER_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _parse_placeholder_date(date_str: str) -> datetime | None:
    """Parse a placeholder date string.

    Format: ``"Dec 25 2025 12:00:00 AM EST"``

    :param date_str: Date string to parse.
    :return: datetime in UTC, or ``None`` if parsing fails.
    """
    # Split off the timezone abbreviation (last token).
    parts = date_str.rsplit(maxsplit=1)
    if len(parts) != 2:
        return None

    date_part, tz_abbrev = parts
    offset = TIMEZONE_OFFSETS.get(tz_abbrev.upper())
    if offset is None:
        return None

    try:
        dt = datetime.strptime(date_part, "%b %d %Y %I:%M:%S %p")
    except ValueError:
        return None

    # Convert to UTC by subtracting the offset.
    return (dt - timedelta(hours=offset)).replace(tzinfo=UTC)


def _format_duration(seconds: int) -> str:
    """Format seconds as human-readable duration.

    Example: ``"1 day 3 hours 20 minutes 30 seconds"``

    :param seconds: Total seconds (positive).
    :return: Human-readable duration string.
    """
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs or not parts:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")

    return " ".join(parts)


async def _evaluate_arg(
    name: str,
    args: list[str],
    ctx: PlaceholderContext,
) -> str:
    """``$(1)`` through ``$(9)`` -- return the positional argument or ``""``."""
    if args:
        raise PlaceholderError(f"Invalid $({name}): does not accept arguments")
    index = int(name) - 1
    if 0 <= index < len(ctx.args_list):
        return ctx.args_list[index]
    return ""


async def _evaluate_user(
    name: str,
    args: list[str],
    ctx: PlaceholderContext,
) -> str:
    """``$(user)`` -- return the invoking user's display name."""
    if args:
        raise PlaceholderError("Invalid $(user): does not accept arguments")
    return ctx.user_display_name


async def _evaluate_count(
    name: str,
    args: list[str],
    ctx: PlaceholderContext,
) -> str:
    """``$(count)`` / ``$(count name [mod])`` -- use count or named counter."""
    if not args:
        # No arguments: return the command's use_count (backward compatible).
        return str(ctx.use_count)

    # Named counter.
    counter_name = args[0].lower()
    if not _COUNTER_NAME_RE.match(counter_name):
        raise PlaceholderError(
            "Invalid $(count): counter name may only contain "
            "letters, numbers, and underscores"
        )
    modifier_str = args[1] if len(args) > 1 else "+1"

    if len(args) > 2:
        raise PlaceholderError(
            "Invalid $(count): too many arguments, expected $(count name [modifier])"
        )

    if modifier_str[0] in ("+", "-"):
        try:
            delta = int(modifier_str)
        except ValueError as e:
            raise PlaceholderError(
                "Invalid $(count): modifier must be an integer (e.g., +5, -1, 0)"
            ) from e
        result = await ctx.storage.fetch_value(
            "INSERT INTO counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = value + ? "
            "RETURNING value",
            (counter_name, delta, delta),
        )
        return str(result)
    else:
        try:
            value = int(modifier_str)
        except ValueError as e:
            raise PlaceholderError(
                "Invalid $(count): modifier must be an integer (e.g., +5, -1, 0)"
            ) from e
        result = await ctx.storage.fetch_value(
            "INSERT OR REPLACE INTO counters (name, value) VALUES (?, ?) "
            "RETURNING value",
            (counter_name, value),
        )
        return str(result)


async def _evaluate_getcount(
    name: str,
    args: list[str],
    ctx: PlaceholderContext,
) -> str:
    """``$(getcount name)`` -- read a named counter's value."""
    if not args:
        raise PlaceholderError("Invalid $(getcount): a counter name is required")
    if len(args) > 1:
        raise PlaceholderError(
            "Invalid $(getcount): too many arguments, expected $(getcount name)"
        )
    counter_name = args[0].lower()
    if not _COUNTER_NAME_RE.match(counter_name):
        raise PlaceholderError(
            "Invalid $(getcount): counter name may only contain "
            "letters, numbers, and underscores"
        )
    row = await ctx.storage.fetch_one(
        "SELECT value FROM counters WHERE name = ?", (counter_name,)
    )
    return str(row["value"]) if row else "0"


async def _evaluate_rand(
    name: str,
    args: list[str],
    ctx: PlaceholderContext,
) -> str:
    """``$(rand start stop)`` -- random integer in range."""
    if len(args) < 2:
        raise PlaceholderError(
            "Invalid $(rand): too few arguments, expected $(rand start stop)"
        )
    if len(args) > 2:
        raise PlaceholderError(
            "Invalid $(rand): too many arguments, expected $(rand start stop)"
        )
    try:
        start = int(args[0])
        stop = int(args[1])
    except ValueError as e:
        raise PlaceholderError(
            "Invalid $(rand): arguments must be integers, e.g., $(rand 1 100)"
        ) from e
    return str(random.randint(min(start, stop), max(start, stop)))


async def _evaluate_countdown(
    name: str,
    args: list[str],
    ctx: PlaceholderContext,
) -> str:
    """``$(countdown date)`` / ``$(countup date)`` -- time delta."""
    date_str = " ".join(args)
    if not date_str.strip():
        raise PlaceholderError(
            f"Invalid $({name}): missing date, expected "
            f"$({name} Dec 25 2025 12:00:00 AM EST)"
        )
    target = _parse_placeholder_date(date_str)
    if target is None:
        raise PlaceholderError(
            f"Invalid $({name}): unrecognized date format, "
            f"expected $({name} Dec 25 2025 12:00:00 AM EST)"
        )
    now = datetime.now(UTC)
    delta = (target - now) if name == "countdown" else (now - target)
    seconds = int(delta.total_seconds())
    if seconds > 0:
        return _format_duration(seconds)
    else:
        return "0 seconds"


type PlaceholderHandler = Callable[
    [str, list[str], "PlaceholderContext"], Awaitable[str]
]

HANDLERS: dict[str, PlaceholderHandler] = {}

for _i in range(1, 10):
    HANDLERS[str(_i)] = _evaluate_arg
HANDLERS["user"] = _evaluate_user
HANDLERS["count"] = _evaluate_count
HANDLERS["getcount"] = _evaluate_getcount
HANDLERS["rand"] = _evaluate_rand
HANDLERS["countdown"] = _evaluate_countdown
HANDLERS["countup"] = _evaluate_countdown
