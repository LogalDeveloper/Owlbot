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

"""Web helpers for Owlbot browser session flows."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

import jinja2
from aiohttp import web

from owlbot import __version__
from owlbot.sessions import (
    SESSION_COOKIE_NAME,
    BrowserSession,
    ConnectTokenRedemptionError,
    SessionManager,
)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_TEMPLATE_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_TEMPLATE_DIR),
    autoescape=True,
)
_TEMPLATE_ENV.globals["owlbot_version"] = __version__


def _render_template(template_name: str, **context: object) -> str:
    template = _TEMPLATE_ENV.get_template(template_name)
    return template.render(**context)


def connect_guidance_response(
    *,
    status: int,
    command_prefix: str = "!",
    title: str = "Connect to Owlbot",
    message: str | None = None,
    command_message: str | None = None,
) -> web.Response:
    """Return a simple connect guidance page for protected routes."""
    body_message = message or "Run this command in chat, then try again."
    return web.Response(
        status=status,
        text=_render_template(
            "connect_error.html",
            title=title,
            body_message=body_message,
            command_message=command_message,
            command_prefix=command_prefix,
        ),
        content_type="text/html",
    )


def browser_session_status_response(
    session: BrowserSession | None,
    *,
    command_prefix: str,
) -> web.Response:
    """Return the browser session status page."""
    return web.Response(
        text=_render_template(
            "session_status.html",
            session=session,
            command_prefix=command_prefix,
        ),
        content_type="text/html",
    )


def register_browser_session_routes(
    app: web.Application,
    *,
    session_manager: SessionManager,
    command_prefix: str,
    cookie_secure: bool = False,
) -> None:
    """Register the shared browser-connection endpoints on the app."""

    async def redeem(request: web.Request) -> web.Response:
        token = request.match_info["token"]
        replacing_session_id = request.cookies.get(SESSION_COOKIE_NAME)
        connect_token = session_manager.get_connect_token(token)
        if connect_token is None:
            return connect_guidance_response(
                status=HTTPStatus.FORBIDDEN,
                command_prefix=command_prefix,
                title="Invalid or expired link",
                message="This connection link is invalid or has expired.",
            )

        try:
            session = session_manager.redeem_connect_token(
                token,
                replacing_session_id=replacing_session_id,
            )
        except ConnectTokenRedemptionError:
            return connect_guidance_response(
                status=HTTPStatus.FORBIDDEN,
                command_prefix=command_prefix,
                title="Invalid or expired link",
                message="This connection link is invalid or has expired.",
            )

        response = web.HTTPFound(location=connect_token.destination_path)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session.session_id,
            httponly=True,
            max_age=int((session.expires_at - session.created_at).total_seconds()),
            samesite="Lax",
            secure=cookie_secure,
            path="/owlbot",
        )
        raise response

    async def status(request: web.Request) -> web.Response:
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        session = session_manager.get_session(session_id)
        return browser_session_status_response(
            session,
            command_prefix=command_prefix,
        )

    app.router.add_get("/owlbot/connect", status)
    app.router.add_get("/owlbot/connect/{token}", redeem)
