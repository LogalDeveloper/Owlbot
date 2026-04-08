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

"""Route handlers for the polls module."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from http import HTTPStatus
from pathlib import Path
from typing import Any

from aiohttp import web

from owlbot.api import RouteContext, on_route

from .manager import PollError, get_manager
from .types import (
    DEFAULT_DURATION,
    MAX_DURATION,
    MAX_OPTION_LENGTH,
    MAX_OPTIONS,
    MAX_QUESTION_LENGTH,
    MIN_DURATION,
    SSEEvent,
    sse_payload,
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_POLLS_CSS_PATH = _STATIC_DIR / "polls.css"
_POLLS_JS_PATH = _STATIC_DIR / "polls.js"
_CREATE_JS_PATH = _STATIC_DIR / "create.js"


def _error_page(ctx: RouteContext, status: int, message: str) -> web.Response:
    """Render a simple error page.

    :param ctx: The route context.
    :param status: HTTP status code.
    :param message: Error message to display.
    :return: HTML error response.
    """
    page = ctx.templates.render("error.html", message=message)
    return web.Response(status=status, text=page, content_type="text/html")


def _default_form() -> dict[str, Any]:
    """Return default form values for the poll creation template."""
    return {
        "question": "",
        "options": ["", ""],
        "duration": DEFAULT_DURATION,
        "hidden": False,
        "requires_auth": False,
        "allow_multiple": False,
        "min_selections": "",
        "max_selections": "",
    }


def _create_form_page(
    ctx: RouteContext,
    token: str,
    *,
    form: dict[str, Any],
    error: str | None = None,
    status: int = 200,
) -> web.Response:
    """Render the poll creation form.

    :param ctx: The route context.
    :param token: The creation token.
    :param form: Form field values to populate.
    :param error: Optional error message to display.
    :param status: HTTP status code.
    :return: HTML response with the creation form.
    """
    constraints = {
        "min_duration": MIN_DURATION,
        "max_duration": MAX_DURATION,
        "max_options": MAX_OPTIONS,
        "max_question_length": MAX_QUESTION_LENGTH,
        "max_option_length": MAX_OPTION_LENGTH,
    }
    page = ctx.templates.render(
        "create.html",
        token=token,
        form=form,
        error=error,
        constraints=constraints,
        submit_url=ctx.routes.url_for(f"/create/{token}"),
        create_js_url=ctx.routes.url_for("/static/create.js"),
    )
    return web.Response(status=status, text=page, content_type="text/html")


@on_route("/create/{token}", methods=["GET"])
async def create_page(ctx: RouteContext) -> web.Response:
    """Serve the poll creation form.

    :param ctx: The route context.
    :return: HTML response with the creation form.
    """
    token = ctx.match_info["token"]
    manager = get_manager(ctx.module)

    if manager.active_poll is not None:
        return _error_page(ctx, HTTPStatus.CONFLICT, "A poll is already active.")

    if manager.creation_token_value is None or not secrets.compare_digest(
        manager.creation_token_value, token
    ):
        return _error_page(
            ctx, HTTPStatus.FORBIDDEN, "Invalid or expired creation link."
        )

    return _create_form_page(ctx, token, form=_default_form())


@on_route("/create/{token}", methods=["POST"])
async def create_submit(ctx: RouteContext) -> web.Response:
    """Handle poll creation form submission.

    Validates inputs, delegates to the manager to create the poll,
    and redirects to the voting page.

    :param ctx: The route context.
    :return: Redirect to the voting page, or error response.
    """
    token = ctx.match_info["token"]
    data = await ctx.request.post()

    # Parse form inputs.
    question = str(data.get("question", "")).strip()

    options_raw = str(data.get("options", "")).strip()
    options = [o.strip() for o in options_raw.splitlines() if o.strip()]

    try:
        duration = int(str(data.get("duration", "0")))
    except ValueError:
        duration = 0

    hidden = str(data.get("hidden", "")) == "on"
    requires_auth = str(data.get("requires_auth", "")) == "on"
    allow_multiple = str(data.get("allow_multiple", "")) == "on"

    min_sel_raw = str(data.get("min_selections", "")).strip()
    max_sel_raw = str(data.get("max_selections", "")).strip()
    min_selections: int | None = None
    max_selections: int | None = None
    if allow_multiple:
        if min_sel_raw:
            try:
                min_selections = int(min_sel_raw)
            except ValueError:
                min_selections = None
        if max_sel_raw:
            try:
                max_selections = int(max_sel_raw)
            except ValueError:
                max_selections = None

    form: dict[str, Any] = {
        "question": question,
        "options": options or ["", ""],
        "duration": duration,
        "hidden": hidden,
        "requires_auth": requires_auth,
        "allow_multiple": allow_multiple,
        "min_selections": min_sel_raw,
        "max_selections": max_sel_raw,
    }

    manager = get_manager(ctx.module)
    try:
        await manager.create_poll(
            token=token,
            question=question,
            options=options,
            hidden=hidden,
            requires_auth=requires_auth,
            allow_multiple=allow_multiple,
            min_selections=min_selections,
            max_selections=max_selections,
            duration=duration,
        )
    except PollError as e:
        return _create_form_page(
            ctx, token, form=form, error=str(e), status=HTTPStatus.BAD_REQUEST
        )

    # Redirect to the mod's voting page.
    vote_url = ctx.routes.url_for(f"/vote/{token}")
    return web.HTTPFound(vote_url)


@on_route("/vote/{token}", methods=["GET"])
async def vote_page(ctx: RouteContext) -> web.Response:
    """Serve the voting page.

    Validates the token and renders the voting page with appropriate
    visibility and permission controls based on the token's privileges.

    :param ctx: The route context.
    :return: HTML response with the voting page.
    """
    manager = get_manager(ctx.module)
    poll = manager.active_poll

    if poll is None:
        return _error_page(ctx, HTTPStatus.NOT_FOUND, "No active poll.")

    token = ctx.match_info["token"]
    token_obj = poll.tokens.get(token)

    if token_obj is None:
        return _error_page(ctx, HTTPStatus.FORBIDDEN, "Invalid or expired voting link.")

    has_voted = token_obj.user_id in poll.votes
    current_votes = poll.votes.get(token_obj.user_id, set())

    page = ctx.templates.render(
        "vote.html",
        poll=poll,
        token=token_obj,
        has_voted=has_voted,
        current_votes=current_votes,
        vote_url=ctx.routes.url_for(f"/vote/{token}"),
        polls_css_url=ctx.routes.url_for("/static/polls.css"),
        polls_js_url=ctx.routes.url_for("/static/polls.js"),
    )
    return web.Response(text=page, content_type="text/html")


@on_route("/vote/{token}", methods=["POST"])
async def vote_submit(ctx: RouteContext) -> web.Response:
    """Handle vote form submission.

    Validates the token, parses selections, delegates to the manager,
    and redirects back to the voting page.

    :param ctx: The route context.
    :return: Redirect back to the voting page, or error response.
    """
    manager = get_manager(ctx.module)
    poll = manager.active_poll

    if poll is None:
        return _error_page(ctx, HTTPStatus.NOT_FOUND, "No active poll.")

    token = ctx.match_info["token"]
    token_obj = poll.tokens.get(token)

    if token_obj is None:
        return _error_page(ctx, HTTPStatus.FORBIDDEN, "Invalid voting link.")

    if poll.requires_auth and not token_obj.is_authenticated:
        return _error_page(
            ctx,
            HTTPStatus.FORBIDDEN,
            "This poll requires an authenticated account to vote.",
        )

    voter_id = token_obj.user_id

    data = await ctx.request.post()

    # Parse selections from form checkboxes/radio buttons.
    raw_selections = data.getall("option", [])
    try:
        selections = {int(str(s)) for s in raw_selections}
    except ValueError:
        return _error_page(ctx, HTTPStatus.BAD_REQUEST, "Invalid selection.")

    try:
        await manager.record_vote(voter_id, selections)
    except PollError as e:
        return _error_page(ctx, HTTPStatus.BAD_REQUEST, str(e))

    # Redirect back to the voting page.
    vote_url = ctx.routes.url_for(f"/vote/{token}")
    return web.HTTPFound(vote_url)


@on_route("/vote/{token}/events", streaming=True)
async def events_stream(ctx: RouteContext) -> web.StreamResponse | web.Response:
    """SSE endpoint for live poll updates.

    Sends keepalive events every 15 seconds with time remaining.

    :param ctx: The route context.
    :return: SSE stream response.
    """
    manager = get_manager(ctx.module)
    poll = manager.active_poll

    if poll is None:
        return web.Response(status=HTTPStatus.NOT_FOUND, text="No active poll.")

    token = ctx.match_info["token"]
    if token not in poll.tokens:
        return web.Response(status=HTTPStatus.FORBIDDEN, text="Invalid token.")

    response = web.StreamResponse(
        status=HTTPStatus.OK,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(ctx.request)

    await manager.register_sse_client(token, response)

    try:
        while manager.active_poll is poll:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(poll.done_event.wait(), timeout=15)
            # If the poll ended, exit immediately.
            if manager.active_poll is not poll:
                break
            try:
                remaining = poll.time_remaining
                payload = sse_payload(SSEEvent.KEEPALIVE, {"time_remaining": remaining})
                await response.write(payload)
            except Exception:
                ctx.logger.debug("SSE keepalive failed for %s...", token[:8])
                break
    finally:
        # Only remove ourselves if we're still the registered client.
        # A page reload creates a new SSE connection under the same token,
        # so the old handler must not evict the replacement.
        if poll.sse_clients.get(token) is response:
            poll.sse_clients.pop(token, None)
        ctx.logger.debug(
            "SSE client disconnected: %s... (%d remaining).",
            token[:8],
            len(poll.sse_clients),
        )

    return response


@on_route("/vote/{token}/end", methods=["POST"])
async def mod_end(ctx: RouteContext) -> web.Response:
    """End the poll via the moderator web UI.

    :param ctx: The route context.
    :return: Redirect to the results page.
    """
    manager = get_manager(ctx.module)
    poll = manager.active_poll

    if poll is None:
        return _error_page(ctx, HTTPStatus.NOT_FOUND, "No active poll.")

    token = ctx.match_info["token"]
    token_obj = poll.tokens.get(token)
    if token_obj is None or not token_obj.is_moderator:
        return _error_page(
            ctx, HTTPStatus.FORBIDDEN, "Only moderators can end the poll."
        )

    await manager.end()
    results_url = ctx.routes.url_for("/results")
    return web.HTTPFound(results_url)


@on_route("/vote/{token}/cancel", methods=["POST"])
async def mod_cancel(ctx: RouteContext) -> web.Response:
    """Cancel the poll via the moderator web UI.

    :param ctx: The route context.
    :return: Redirect response or error page.
    """
    manager = get_manager(ctx.module)
    poll = manager.active_poll

    if poll is None:
        return _error_page(ctx, HTTPStatus.NOT_FOUND, "No active poll.")

    token = ctx.match_info["token"]
    token_obj = poll.tokens.get(token)
    if token_obj is None or not token_obj.is_moderator:
        return _error_page(
            ctx, HTTPStatus.FORBIDDEN, "Only moderators can cancel the poll."
        )

    await manager.cancel()
    return _error_page(ctx, HTTPStatus.OK, "This poll has been cancelled.")


@on_route("/results", methods=["GET"])
async def results_page(ctx: RouteContext) -> web.Response:
    """Serve the results page for the most recently completed poll.

    Shows contextual error messages if a poll is active or no
    results are available.

    :param ctx: The route context.
    :return: HTML response with the results page.
    """
    manager = get_manager(ctx.module)

    if manager.active_poll is not None:
        return _error_page(ctx, HTTPStatus.OK, "A poll is currently in progress.")

    result = manager.last_result
    if result is None:
        return _error_page(ctx, HTTPStatus.OK, "No poll has been done recently.")

    polls_css_url = ctx.routes.url_for("/static/polls.css")
    page = ctx.templates.render(
        "results.html",
        question=result.question,
        options=result.options,
        results=result.results,
        created_at=result.created_at.isoformat(),
        ended_at=result.ended_at.isoformat(),
        polls_css_url=polls_css_url,
    )
    return web.Response(text=page, content_type="text/html")


@on_route("/static/polls.css", methods=["GET"])
async def static_polls_css(ctx: RouteContext) -> web.StreamResponse:
    """Serve the polls stylesheet.

    :param ctx: The route context.
    :return: The polls.css file with caching headers.
    """
    return web.FileResponse(
        _POLLS_CSS_PATH,
        headers={"Cache-Control": "max-age=86400"},
    )


@on_route("/static/polls.js", methods=["GET"])
async def static_polls_js(ctx: RouteContext) -> web.StreamResponse:
    """Serve the polls JavaScript file.

    :param ctx: The route context.
    :return: The polls.js file with caching headers.
    """
    return web.FileResponse(
        _POLLS_JS_PATH,
        headers={"Cache-Control": "max-age=86400"},
    )


@on_route("/static/create.js", methods=["GET"])
async def static_create_js(ctx: RouteContext) -> web.StreamResponse:
    """Serve the create form JavaScript file.

    :param ctx: The route context.
    :return: The create.js file with caching headers.
    """
    return web.FileResponse(
        _CREATE_JS_PATH,
        headers={"Cache-Control": "max-age=86400"},
    )
