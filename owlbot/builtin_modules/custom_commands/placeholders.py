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

"""Recursive descent placeholder processing for custom commands.

This module implements an AST-based pipeline for parsing and evaluating
placeholders in custom command response templates.  The pipeline has two
stages:

1. **Parser**: A recursive descent parser (``_parse``) converts a template
   string into a list of ``Node`` objects (``TextNode`` for literal text,
   ``PlaceholderNode`` for ``$(...)`` expressions).  Nesting is supported up to
   a configurable maximum depth.

2. **Evaluator**: An async tree-walker (``_evaluate``) resolves the AST
   inside-out: children of each ``PlaceholderNode`` are evaluated first, then
   the resulting flat content string is dispatched to the matching handler in
   ``placeholder_handlers`` for final resolution.

Supported placeholders: ``$(1)``-``$(9)``, ``$(count)``, ``$(count name [mod])``,
``$(getcount name)``, ``$(user)``, ``$(rand start stop)``, ``$(countdown date)``,
``$(countup date)``.

Example AST::

    Template: "$(rand $(1) $(2))"
    Parsed:   [PlaceholderNode("rand", [PlaceholderNode("1", []),
                                        TextNode(" "),
                                        PlaceholderNode("2", [])])]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from owlbot.api.storage import ModuleStorage

from .placeholder_handlers import HANDLERS, PlaceholderError

DEFAULT_MAX_DEPTH: int = 4


@dataclass(slots=True)
class TextNode:
    """A span of literal text that needs no further processing.

    :param text: The literal text content.
    """

    text: str


@dataclass(slots=True)
class PlaceholderNode:
    """A ``$(name ...)`` placeholder expression.

    :param name: The placeholder name, extracted as literal text from the
        template (e.g. ``"rand"``, ``"1"``, ``"user"``).  Never contains
        nested placeholders.
    :param children: The parsed body content after the name.  May contain
        nested ``PlaceholderNode`` instances (for dynamic arguments) or be
        empty for no-argument placeholders like ``$(user)``.
    """

    name: str
    children: list[Node]


type Node = TextNode | PlaceholderNode


@dataclass(slots=True)
class PlaceholderContext:
    """Bundles the runtime state needed to resolve placeholders.

    :param args_list: Command arguments (``$(1)``-``$(9)`` values).
    :param user_display_name: Display name of the invoking user.
    :param use_count: The command's current use count.
    :param storage: Module storage for database access.
    """

    args_list: list[str]
    user_display_name: str
    use_count: int
    storage: ModuleStorage


# The parser uses recursive descent to convert a template string into a list
# of Node objects.  It handles escaping (``\$(...)``), nesting (``$(rand
# $(1) $(2))``), unclosed placeholders (degraded to literal text), and a
# configurable maximum nesting depth.


def _find_matching_close(template: str, pos: int) -> int:
    r"""Find the position of the ``)`` that closes an escaped ``\$(...)`` group.

    Tracks nested ``$(`` / ``)`` pairs so that escaped groups containing inner
    placeholders (e.g. ``\$(rand $(1) $(2))``) are consumed in their entirety.

    :param template: The full template string.
    :param pos: The position immediately after the opening ``$(`` of the
        escaped group (i.e. the first character of the content).
    :return: The index of the matching ``)``, or ``-1`` if not found.
    """
    depth = 1
    i = pos
    length = len(template)
    while i < length:
        if template[i : i + 2] == "$(":
            depth += 1
            i += 2
        elif template[i] == ")":
            depth -= 1
            if depth == 0:
                return i
            i += 1
        else:
            i += 1
    return -1


def _parse_placeholder(
    template: str,
    pos: int,
    depth: int,
    max_depth: int,
) -> tuple[PlaceholderNode, int] | None:
    """Parse a single placeholder after the opening ``$(`` has been consumed.

    Reads the placeholder name (any characters up to a space, ``)``, ``$``,
    or end-of-string), then parses children if the name is followed by a space.

    :param template: The full template string.
    :param pos: Position immediately after ``$(`` (start of the name).
    :param depth: Current nesting depth.
    :param max_depth: Maximum allowed nesting depth.
    :return: A ``(PlaceholderNode, new_pos)`` tuple, or ``None`` if the
        placeholder is invalid (e.g. empty name).
    """
    length = len(template)

    # Read the placeholder name: any characters up to a delimiter.
    name_start = pos
    while pos < length and template[pos] not in (" ", ")", "$"):
        pos += 1

    name = template[name_start:pos]

    # Empty name (e.g. $() or $($(...))).  Degrade to literal.
    if not name:
        return None

    # No arguments: immediate close or end of string.
    if pos >= length:
        # Unclosed placeholder at end of string.  Return None so the
        # caller degrades "$(" to literal text; the name characters will
        # be re-scanned as literals since the caller's pos only advances
        # past "$(".
        return None

    if template[pos] == ")":
        # $(name) -- no children.
        return PlaceholderNode(name, []), pos + 1

    if template[pos] == " ":
        # $(name ... ) -- parse children after the space delimiter.
        child_nodes, new_pos, found_close = _parse_nodes(
            template,
            pos + 1,
            depth + 1,
            inside_placeholder=True,
            max_depth=max_depth,
        )
        if found_close:
            return PlaceholderNode(name, child_nodes), new_pos
        # Unclosed placeholder, degrade to literal.  Return None so
        # the caller emits "$(" as literal and re-scans the rest.
        return None

    # The character after the name is something unexpected (e.g. another $).
    # Treat as unclosed/invalid -- degrade.
    if template[pos : pos + 2] == "$(":
        # Something like $(name$(...)) with no space.  Degrade.
        return None

    return None


def _parse_nodes(
    template: str,
    pos: int,
    depth: int,
    inside_placeholder: bool,
    max_depth: int,
) -> tuple[list[Node], int, bool]:
    """Core recursive parser loop.

    Scans *template* starting at *pos*, accumulating literal characters and
    recognising ``$(...)`` placeholder openings.

    :param template: The full template string.
    :param pos: Current scan position.
    :param depth: Current nesting depth (0 = top level).
    :param inside_placeholder: ``True`` when parsing the children of a
        ``PlaceholderNode`` -- a bare ``)`` closes the current group.
    :param max_depth: Maximum allowed nesting depth.
    :return: A 3-tuple ``(nodes, new_pos, found_close)`` where *found_close*
        is ``True`` if scanning stopped because a matching ``)`` was found.
    """
    nodes: list[Node] = []
    buf: list[str] = []
    length = len(template)

    def flush_buffer() -> None:
        """Flush accumulated literal characters as a TextNode."""
        if buf:
            nodes.append(TextNode("".join(buf)))
            buf.clear()

    while pos < length:
        # Escaped placeholder: \$(...) becomes literal text.
        if template[pos] == "\\" and template[pos + 1 : pos + 3] == "$(":
            # Find the matching close paren, accounting for inner $( pairs.
            close = _find_matching_close(template, pos + 3)
            if close == -1:
                # No matching close, treat everything from here as literal.
                buf.append(template[pos:])
                pos = length
            else:
                # Emit the content (without the leading backslash) as literal.
                buf.append(template[pos + 1 : close + 1])
                pos = close + 1
            continue

        # Placeholder opening: $(
        if template[pos : pos + 2] == "$(":
            # If we've hit the nesting limit, treat $( as literal text.
            if depth >= max_depth:
                buf.append("$(")
                pos += 2
                continue

            flush_buffer()

            # Delegate to _parse_placeholder for name extraction and children.
            result = _parse_placeholder(template, pos + 2, depth, max_depth)
            if result is None:
                # Failed to parse a valid placeholder (empty name, etc.).
                # Degrade the $( to literal text and continue scanning.
                buf.append("$(")
                pos += 2
            else:
                node, pos = result
                nodes.append(node)
            continue

        # Closing paren while inside a placeholder's children.
        if template[pos] == ")" and inside_placeholder:
            flush_buffer()
            return nodes, pos + 1, True

        # Ordinary character: accumulate into the literal buffer.
        buf.append(template[pos])
        pos += 1

    flush_buffer()
    return nodes, pos, False


def _parse(template: str, max_depth: int = DEFAULT_MAX_DEPTH) -> list[Node]:
    """Parse a template string into an AST of ``Node`` objects.

    This is the entry point for the parser stage.

    :param template: The response template with placeholders.
    :param max_depth: Maximum nesting depth for placeholders.  ``$(`` tokens
        encountered at or beyond this depth are treated as literal text.
    :return: List of top-level nodes.

    Example::

        >>> _parse("Hello $(user)!")
        [TextNode("Hello "), PlaceholderNode("user", []), TextNode("!")]

        >>> _parse("$(rand $(1) $(2))")
        [PlaceholderNode("rand", [PlaceholderNode("1", []),
                                  TextNode(" "),
                                  PlaceholderNode("2", [])])]
    """
    nodes, _, _ = _parse_nodes(
        template, 0, 0, inside_placeholder=False, max_depth=max_depth
    )
    return nodes


# Evaluation proceeds inside-out: for each PlaceholderNode the evaluator first
# recursively evaluates all children to produce a flat args string, then
# dispatches to the matching handler from HANDLERS.


async def _evaluate_placeholder(
    node: PlaceholderNode,
    ctx: PlaceholderContext,
) -> str:
    """Evaluate a single ``PlaceholderNode``.

    Children are evaluated first (inside-out) and the resulting string is
    split on whitespace to form the argument list.  The handler for the
    placeholder name is then looked up and called with those arguments.

    :param node: The placeholder node to evaluate.
    :param ctx: Runtime context for placeholder resolution.
    :return: The resolved replacement string.
    """
    name = node.name.lower()
    if node.children:
        args_str = await _evaluate(node.children, ctx)
        args = args_str.split()
    else:
        args = []

    handler = HANDLERS.get(name)
    if handler is None:
        content = name + (" " + " ".join(args) if args else "")
        return f"$({content})"

    return await handler(name, args, ctx)


async def _evaluate(nodes: list[Node], ctx: PlaceholderContext) -> str:
    """Walk the AST and produce the final output string.

    :param nodes: List of parsed nodes from ``_parse``.
    :param ctx: Runtime context for placeholder resolution.
    :return: The fully resolved string.
    """
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, TextNode):
            parts.append(node.text)
        else:
            parts.append(await _evaluate_placeholder(node, ctx))
    return "".join(parts)


async def process_placeholders(
    template: str,
    args_list: list[str],
    user_display_name: str,
    use_count: int,
    storage: ModuleStorage,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> str:
    """Replace placeholders in a response template.

    Parses the template into an AST, then evaluates it to produce the final
    output string with all placeholders resolved.

    If any placeholder raises :exc:`PlaceholderError`, evaluation stops
    immediately and the error message is returned as the entire response.

    :param template: The response template with placeholders.
    :param args_list: List of arguments passed to the command.
    :param user_display_name: The executing user's display name.
    :param use_count: The command's current use count.
    :param storage: Module storage for database access.
    :param max_depth: Maximum nesting depth for placeholders.
    :return: The processed response string, or the error message on failure.
    """
    nodes = _parse(template, max_depth=max_depth)
    ctx = PlaceholderContext(
        args_list=args_list,
        user_display_name=user_display_name,
        use_count=use_count,
        storage=storage,
    )
    try:
        return await _evaluate(nodes, ctx)
    except PlaceholderError as e:
        return str(e)
