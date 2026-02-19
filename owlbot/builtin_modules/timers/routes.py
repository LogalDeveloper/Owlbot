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

"""Web routes for the timers module."""

from datetime import datetime
from pathlib import Path

import jinja2
from aiohttp import web

from owlbot.api import RouteContext, on_route

_template_dir = Path(__file__).resolve().parent / "templates"
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_template_dir),
    autoescape=True,
)


@on_route("/list", methods=["GET"])
async def timer_list_page(ctx: RouteContext) -> web.Response:
    """Serve an HTML page listing all timers in a table.

    Columns: #, Name, Message, Interval, Min Lines, Status, Last Fired.
    Accessible at /owlbot/timers/list.

    :param ctx: The route context.
    :return: HTML response with the timer list table.
    """
    rows = await ctx.storage.fetch_all(
        "SELECT id, name, message, interval_type, interval_value, "
        "min_chat_lines, enabled, last_fired_at "
        "FROM timers ORDER BY id"
    )

    timers = []
    for row in rows:
        last_fired = row["last_fired_at"]
        if last_fired:
            last_fired = datetime.fromisoformat(last_fired).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        else:
            last_fired = "Never"

        timers.append(
            {
                "id": row["id"],
                "name": row["name"] or "",
                "message": row["message"] or "(not set)",
                "interval": f"{row['interval_value']} ({row['interval_type']})",
                "min_lines": row["min_chat_lines"],
                "status": "Enabled" if row["enabled"] else "Disabled",
                "last_fired": last_fired,
            }
        )

    template = _jinja_env.get_template("list.html")
    page = template.render(timers=timers)

    return web.Response(text=page, content_type="text/html")
