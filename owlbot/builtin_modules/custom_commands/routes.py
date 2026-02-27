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

"""Web routes for the custom commands module."""

from aiohttp import web

from owlbot.api import RouteContext, on_route


@on_route("/list", methods=["GET"])
async def command_list_page(ctx: RouteContext) -> web.Response:
    """Serve an HTML page listing all custom commands in a table.

    Columns: Command, Aliases, Response, Cooldown, Permissions.
    Accessible at /owlbot/custom_commands/list.

    :param ctx: The route context.
    :return: HTML response with the command list table.
    """
    rows = await ctx.storage.fetch_all(
        "SELECT c.name, c.response, c.requires_moderator, c.cooldown, "
        "GROUP_CONCAT(ca.alias, ', ') AS aliases "
        "FROM commands c "
        "LEFT JOIN command_aliases ca ON c.name = ca.command_name "
        "GROUP BY c.name "
        "ORDER BY c.name"
    )

    prefix = ctx.commands.prefix
    commands = [
        {
            "name": row["name"],
            "aliases": (
                ", ".join(f"{prefix}{a}" for a in row["aliases"].split(", "))
                if row["aliases"]
                else "None"
            ),
            "response": row["response"],
            "permissions": "Moderator" if row["requires_moderator"] else "Everyone",
            "cooldown": "None" if row["cooldown"] == 0 else f"{row['cooldown']}s",
        }
        for row in rows
    ]

    page = ctx.templates.render("list.html", commands=commands, prefix=prefix)
    return web.Response(text=page, content_type="text/html")
