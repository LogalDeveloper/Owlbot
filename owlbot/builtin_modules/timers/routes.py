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

from aiohttp import web

from owlbot.api import RouteContext, on_route

from .manager import get_manager


@on_route("/list", methods=["GET"])
async def timer_list_page(ctx: RouteContext) -> web.Response:
    """Serve an HTML page listing all timers in a table.

    Columns: #, Name, Message, Interval, Min Lines, Status, Last Fired.
    Accessible at /owlbot/timers/list.

    :param ctx: The route context.
    :return: HTML response with the timer list table.
    """
    timers = await get_manager(ctx.module).list_timers()
    page = ctx.templates.render("list.html", timers=timers)
    return web.Response(text=page, content_type="text/html")
