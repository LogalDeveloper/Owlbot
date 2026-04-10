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

"""Chat commands for managing quotes."""

from owlbot.api import CommandContext, on_command

from .manager import get_manager
from .types import QuoteNotFoundError


@on_command("quote", aliases=["q"])
async def quote_command(ctx: CommandContext) -> None:
    """Display a quote. Random if no argument, specific if an ID is given.

    Usage: !quote [id]

    :param ctx: The command context.
    """
    args = ctx.args_list

    if not args:
        quote = await get_manager(ctx.module).get_random_quote()
        if quote is None:
            await ctx.owncast_client.send_message("No quotes have been added yet.")
            return
        await ctx.owncast_client.send_message(f'"{quote.text}" (#{quote.id})')
        return

    try:
        quote_id = int(args[0])
    except ValueError:
        await ctx.owncast_client.send_message("Usage: !quote [id]")
        return

    try:
        quote = await get_manager(ctx.module).get_quote(quote_id)
    except QuoteNotFoundError as e:
        await ctx.owncast_client.send_message(f"Quote #{e.quote_id} not found.")
        return

    await ctx.owncast_client.send_message(
        f'Quote #{quote.id}: "{quote.text}" - Added by {quote.added_by} on {quote.date}'
    )


@on_command("addquote", requires_moderator=True)
async def addquote_command(ctx: CommandContext) -> None:
    """Add a new quote to the database. Moderator only.

    Usage: !addquote <quote text>

    :param ctx: The command context.
    """
    quote_text = ctx.args.strip()
    if not quote_text:
        await ctx.owncast_client.send_message("Usage: !addquote <quote text>")
        return

    quote = await get_manager(ctx.module).add_quote(quote_text, ctx.user.display_name)
    await ctx.owncast_client.send_message(f"Quote #{quote.id} added.")


@on_command("deletequote", aliases=["delquote"], requires_moderator=True)
async def deletequote_command(ctx: CommandContext) -> None:
    """Delete a quote by ID. Moderator only.

    Usage: !deletequote <id>

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

    try:
        await get_manager(ctx.module).delete_quote(quote_id)
    except QuoteNotFoundError as e:
        await ctx.owncast_client.send_message(f"Quote #{e.quote_id} not found.")
        return

    await ctx.owncast_client.send_message(f"Quote #{quote_id} deleted.")


@on_command("listquotes", cooldown=15)
async def listquotes_command(ctx: CommandContext) -> None:
    """Send the URL to the quotes list web page.

    Usage: !listquotes

    :param ctx: The command context.
    """
    url = ctx.routes.url_for("/list")
    await ctx.owncast_client.send_message(f"Quotes: {url}")
