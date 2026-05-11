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

from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from owlbot.api import RouteContext, on_route

from .manager import get_manager
from .types import (
    DEFAULT_DURATION,
    MAX_DURATION,
    MAX_OPTION_LENGTH,
    MAX_OPTIONS,
    MAX_QUESTION_LENGTH,
    MIN_DURATION,
    MIN_OPTIONS,
    NoActivePollError,
    PollAlreadyActiveError,
    PollAuthenticationRequiredError,
    PollCreateValidationError,
    PollVoteError,
)

if TYPE_CHECKING:
    from owlbot.sessions import BrowserSession

    from .manager import PollManager

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_POLLS_CSS_PATH = _STATIC_DIR / "polls.css"
_POLLS_JS_PATH = _STATIC_DIR / "polls.js"
_CREATE_JS_PATH = _STATIC_DIR / "create.js"
_STATIC_FILES = {
    "polls.css": _POLLS_CSS_PATH,
    "polls.js": _POLLS_JS_PATH,
    "create.js": _CREATE_JS_PATH,
    "shared/config.js": _STATIC_DIR / "shared" / "config.js",
    "shared/dom.js": _STATIC_DIR / "shared" / "dom.js",
    "shared/poll-events.js": _STATIC_DIR / "shared" / "poll-events.js",
}


def _error_page(ctx: RouteContext, status: int, message: str) -> web.Response:
    """Render a simple error page.

    :param ctx: The route context.
    :param status: HTTP status code.
    :param message: Error message to display.
    :return: HTML error response.
    """
    page = ctx.templates.render("error.html", message=message)
    return web.Response(status=status, text=page, content_type="text/html")


def _require_session(ctx: RouteContext) -> BrowserSession:
    """Return the browser session for a protected polls route.

    :param ctx: The route context.
    :return: The resolved browser session.
    :raises RuntimeError: If a protected route was called without a session.
    """
    session = ctx.session
    if session is None:
        raise RuntimeError("Protected polls route called without a browser session.")
    return session


def _create_form_page(
    ctx: RouteContext,
    *,
    form: dict[str, Any],
    error: str | None = None,
    status: int = 200,
) -> web.Response:
    """Render the poll creation form.

    :param ctx: The route context.
    :param form: Form field values to populate.
    :param error: Optional error message to display.
    :param status: HTTP status code.
    :return: HTML response with the creation form.
    """
    constraints = {
        "min_duration": MIN_DURATION,
        "max_duration": MAX_DURATION,
        "min_options": MIN_OPTIONS,
        "max_options": MAX_OPTIONS,
        "max_question_length": MAX_QUESTION_LENGTH,
        "max_option_length": MAX_OPTION_LENGTH,
    }
    page = ctx.templates.render(
        "create.html",
        form=form,
        error=error,
        constraints=constraints,
        submit_url=ctx.routes.url_for("/create"),
        create_js_url=ctx.routes.url_for("/static/create.js"),
    )
    return web.Response(status=status, text=page, content_type="text/html")


def _current_poll_page(
    ctx: RouteContext,
    manager: PollManager,
    session: BrowserSession,
    *,
    error: str | None = None,
    status: int = HTTPStatus.OK,
    show_current_selections: bool = True,
) -> web.Response:
    """Render the current poll page from the latest active-poll state.

    This helper keeps route handlers from holding an ``ActivePoll`` reference
    across awaits and then mixing it with fresh manager state. If the poll has
    ended before rendering, the caller gets the normal no-active-poll response.

    :param ctx: The route context.
    :param manager: Poll manager used to read the latest active poll state.
    :param session: Browser session for the viewer rendering the page.
    :param error: Optional inline voting error to display.
    :param status: HTTP status code for the rendered vote page.
    :param show_current_selections: Whether to load and show the viewer's
        existing selections.
    :return: HTML response for the active poll, or a no-active-poll error page.
    """
    poll = manager.active_poll
    if poll is None:
        return _error_page(ctx, HTTPStatus.NOT_FOUND, "No active poll.")

    current_selections: frozenset[int] | None = None
    if show_current_selections:
        try:
            current_selections = manager.get_selections(session.user)
        except NoActivePollError:
            return _error_page(ctx, HTTPStatus.NOT_FOUND, "No active poll.")

    has_voted = current_selections is not None
    page = ctx.templates.render(
        "vote.html",
        poll=poll,
        viewer_is_authenticated=session.is_authenticated,
        viewer_is_moderator=session.is_moderator,
        has_voted=has_voted,
        current_selections=current_selections or frozenset(),
        error=error,
        vote_url=ctx.routes.url_for("/current/vote"),
        events_url=ctx.routes.url_for("/current/events"),
        end_url=ctx.routes.url_for("/current/end"),
        cancel_url=ctx.routes.url_for("/current/cancel"),
        polls_css_url=ctx.routes.url_for("/static/polls.css"),
        polls_js_url=ctx.routes.url_for("/static/polls.js"),
    )
    return web.Response(status=status, text=page, content_type="text/html")


@on_route("/create", methods=["GET"], requires_moderator=True)
async def create_page(ctx: RouteContext) -> web.Response:
    """Serve the poll creation form.

    :param ctx: The route context.
    :return: HTML response with the creation form, or an error response if a
        poll is already active.
    """
    manager = get_manager(ctx.module)

    if manager.active_poll is not None:
        return _error_page(ctx, HTTPStatus.CONFLICT, "A poll is already active.")

    return _create_form_page(
        ctx,
        form={
            "question": "",
            "options": ["", ""],
            "duration": DEFAULT_DURATION,
            "hidden": False,
            "requires_auth": False,
            "allow_multiple": False,
            "min_selections": "",
            "max_selections": "",
        },
    )


@on_route("/create", methods=["POST"], requires_moderator=True)
async def create_submit(ctx: RouteContext) -> web.Response:
    """Handle poll creation form submission.

    Parses form inputs, delegates normalized fields to the manager, and
    redirects to the current poll page.

    :param ctx: The route context.
    :return: Redirect to the current poll page, or error response.
    """
    data = await ctx.request.post()

    # Parse form inputs.
    question = str(data.get("question", "")).strip()

    options = [str(option).strip() for option in data.getall("options", [])]

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
            question=question,
            options=options,
            hidden=hidden,
            requires_auth=requires_auth,
            allow_multiple=allow_multiple,
            min_selections=min_selections,
            max_selections=max_selections,
            duration=duration,
        )
    except PollAlreadyActiveError:
        return _create_form_page(
            ctx,
            form=form,
            error="A poll is already active.",
            status=HTTPStatus.CONFLICT,
        )
    except PollCreateValidationError as e:
        return _create_form_page(
            ctx, form=form, error=str(e), status=HTTPStatus.BAD_REQUEST
        )

    # Redirect to the active poll page.
    current_url = ctx.routes.url_for("/current")
    raise web.HTTPFound(current_url)


@on_route("/current", methods=["GET"], requires_session=True)
async def current_page(ctx: RouteContext) -> web.Response:
    """Serve the current poll page.

    Renders the poll page with appropriate visibility and permission controls
    based on the connected viewer's session.

    :param ctx: The route context.
    :return: HTML response with the current poll page, or an error response if
        no poll is active.
    """
    manager = get_manager(ctx.module)
    session = _require_session(ctx)
    return _current_poll_page(ctx, manager, session)


@on_route("/current/vote", methods=["POST"], requires_session=True)
async def vote_submit(ctx: RouteContext) -> web.Response:
    """Handle vote form submission.

    Parses selections, delegates to the manager, and redirects back to the
    current poll page.

    :param ctx: The route context.
    :return: Redirect back to the current poll page, or error response.
    """
    manager = get_manager(ctx.module)
    session = _require_session(ctx)
    data = await ctx.request.post()

    # Parse selections from form checkboxes/radio buttons.
    raw_selections = data.getall("option", [])
    try:
        selections = {int(str(s)) for s in raw_selections}
    except ValueError:
        return _current_poll_page(
            ctx,
            manager,
            session=session,
            error="Invalid selection.",
            status=HTTPStatus.BAD_REQUEST,
        )

    try:
        await manager.record_vote(
            session.user,
            selections,
        )
    except NoActivePollError:
        return _error_page(ctx, HTTPStatus.NOT_FOUND, "No active poll.")
    except PollAuthenticationRequiredError:
        return _current_poll_page(
            ctx,
            manager,
            session=session,
            show_current_selections=False,
            error="This poll requires an authenticated account to vote.",
            status=HTTPStatus.FORBIDDEN,
        )
    except PollVoteError as e:
        return _current_poll_page(
            ctx,
            manager,
            session=session,
            error=str(e),
            status=HTTPStatus.BAD_REQUEST,
        )

    # Redirect back to the active poll page.
    current_url = ctx.routes.url_for("/current")
    raise web.HTTPFound(current_url)


@on_route("/current/events", streaming=True, requires_session=True)
async def events_stream(ctx: RouteContext) -> web.StreamResponse | web.Response:
    """SSE endpoint for live poll updates.

    :param ctx: The route context.
    :return: SSE stream response, or a 404 response if no poll is active.
    """
    manager = get_manager(ctx.module)
    poll = manager.active_poll

    if poll is None:
        return web.Response(status=HTTPStatus.NOT_FOUND, text="No active poll.")

    session = _require_session(ctx)

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

    try:
        await manager.serve_sse_stream(session.user, response)
    except NoActivePollError:
        ctx.logger.debug("SSE stream ended before client registration.")

    return response


@on_route("/current/end", methods=["POST"], requires_moderator=True)
async def mod_end(ctx: RouteContext) -> web.Response:
    """End the poll via the moderator web UI.

    :param ctx: The route context.
    :return: Redirect to the results page, or an error response if no poll is
        active.
    """
    manager = get_manager(ctx.module)
    try:
        await manager.end()
    except NoActivePollError:
        return _error_page(ctx, HTTPStatus.NOT_FOUND, "No active poll.")

    results_url = ctx.routes.url_for("/results")
    raise web.HTTPFound(results_url)


@on_route("/current/cancel", methods=["POST"], requires_moderator=True)
async def mod_cancel(ctx: RouteContext) -> web.Response:
    """Cancel the poll via the moderator web UI.

    :param ctx: The route context.
    :return: Cancellation confirmation or error page.
    """
    manager = get_manager(ctx.module)
    try:
        await manager.cancel()
    except NoActivePollError:
        return _error_page(ctx, HTTPStatus.NOT_FOUND, "No active poll.")

    return _error_page(ctx, HTTPStatus.OK, "This poll has been cancelled.")


@on_route("/results", methods=["GET"])
async def results_page(ctx: RouteContext) -> web.Response:
    """Serve the results page for the most recently completed poll.

    Shows contextual messages if a poll is active or no results are available.

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


@on_route("/static/{filename:.+}", methods=["GET"])
async def static_asset(ctx: RouteContext) -> web.StreamResponse | web.Response:
    """Serve allow-listed poll module static assets.

    :param ctx: The route context containing the requested filename.
    :return: Static file response, or 404 for unknown asset names.
    """
    filename = ctx.match_info["filename"]
    path = _STATIC_FILES.get(filename)
    if path is None:
        return web.Response(status=HTTPStatus.NOT_FOUND)
    cache_control = "no-cache" if filename.startswith("shared/") else "max-age=86400"
    return web.FileResponse(
        path,
        headers={"Cache-Control": cache_control},
    )
