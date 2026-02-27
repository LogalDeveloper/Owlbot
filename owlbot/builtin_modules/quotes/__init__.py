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

"""Quotes module for Owlbot.

Allows moderators to store quotes and anyone to recall random quotes.
"""

from datetime import UTC, datetime

from aiohttp import web

from owlbot.api import (
    CommandContext,
    ModuleContext,
    RouteContext,
    on_command,
    on_route,
    on_setup,
)


@on_setup
async def setup(ctx: ModuleContext) -> None:
    """Initialize the quotes module.

    Creates the database schema.

    :param ctx: Module context with config, storage, and other services.
    """
    await ctx.storage.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            added_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    count = await ctx.storage.fetch_value("SELECT COUNT(*) FROM quotes")
    ctx.logger.info(f"Loaded {count} quote(s) from database.")


@on_route("/list", methods=["GET"])
async def quotes_list_page(ctx: RouteContext) -> web.Response:
    """Serve an HTML page listing all quotes in a table.

    Columns: #, Quote, Added By, Date Added.
    Accessible at /owlbot/quotes/list.

    :param ctx: The route context.
    :return: HTML response with the quotes list table.
    """
    rows = await ctx.storage.fetch_all(
        "SELECT id, text, added_by, created_at FROM quotes ORDER BY id"
    )

    quotes = [
        {
            "id": row["id"],
            "text": row["text"],
            "added_by": row["added_by"],
            "date": datetime.fromisoformat(row["created_at"]).strftime("%Y-%m-%d"),
        }
        for row in rows
    ]

    page = ctx.templates.render("list.html", quotes=quotes)
    return web.Response(text=page, content_type="text/html")


@on_command("quote", aliases=["q"])
async def quote_command(ctx: CommandContext) -> None:
    """Display a quote. Random if no argument, specific if an ID is given.

    :param ctx: The command context.
    """
    args = ctx.args_list

    if not args:
        row = await ctx.storage.fetch_one(
            "SELECT id, text FROM quotes ORDER BY RANDOM() LIMIT 1"
        )

        if not row:
            await ctx.owncast_client.send_message("No quotes have been added yet.")
            return

        await ctx.owncast_client.send_message(f'"{row["text"]}" (#{row["id"]})')
        return

    try:
        quote_id = int(args[0])
    except ValueError:
        await ctx.owncast_client.send_message("Usage: !quote [id]")
        return

    row = await ctx.storage.fetch_one(
        "SELECT id, text, added_by, created_at FROM quotes WHERE id = ?", (quote_id,)
    )
    if not row:
        await ctx.owncast_client.send_message(f"Quote #{quote_id} not found.")
        return

    created_at = datetime.fromisoformat(row["created_at"])
    date_str = created_at.strftime("%Y-%m-%d")

    await ctx.owncast_client.send_message(
        f'Quote #{row["id"]}: "{row["text"]}" '
        f"- Added by {row['added_by']} on {date_str}"
    )


@on_command("addquote", requires_moderator=True)
async def addquote_command(ctx: CommandContext) -> None:
    """Add a new quote to the database. Moderator only.

    :param ctx: The command context.
    """
    quote_text = ctx.args.strip()
    if not quote_text:
        await ctx.owncast_client.send_message("Usage: !addquote <quote text>")
        return

    now = datetime.now(UTC).isoformat()
    cursor = await ctx.storage.execute(
        "INSERT INTO quotes (text, added_by, created_at) VALUES (?, ?, ?)",
        (quote_text, ctx.user.display_name, now),
    )

    ctx.logger.info(f"Quote #{cursor.lastrowid} added by {ctx.user.display_name}.")
    await ctx.owncast_client.send_message(f"Quote #{cursor.lastrowid} added.")


@on_command("deletequote", aliases=["delquote"], requires_moderator=True)
async def deletequote_command(ctx: CommandContext) -> None:
    """Delete a quote by ID. Moderator only.

    :param ctx: The command context.
    """
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message("Usage: !deletequote <id>")
        return

    try:
        quote_id = int(args[0])
    except ValueError:
        await ctx.owncast_client.send_message("Usage: !deletequote <id>")
        return

    existing = await ctx.storage.fetch_one(
        "SELECT id FROM quotes WHERE id = ?", (quote_id,)
    )
    if not existing:
        await ctx.owncast_client.send_message(f"Quote #{quote_id} not found.")
        return

    await ctx.storage.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))

    ctx.logger.info(f"Quote #{quote_id} deleted by {ctx.user.display_name}.")
    await ctx.owncast_client.send_message(f"Quote #{quote_id} deleted.")


@on_command("listquotes", cooldown=15)
async def listquotes_command(ctx: CommandContext) -> None:
    """Send the URL to the quotes list web page.

    :param ctx: The command context.
    """
    url = ctx.routes.url_for("/list")
    await ctx.owncast_client.send_message(f"Quotes: {url}")
