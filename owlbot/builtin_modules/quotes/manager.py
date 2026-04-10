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

"""Business logic coordinator for the quotes module."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from owlbot.api import ModuleContext

    from .repository import QuoteRepository
    from .types import Quote


class QuoteManager:
    """Coordinates between the repository and handlers."""

    def __init__(self, ctx: ModuleContext, repo: QuoteRepository) -> None:
        """Initialize the manager.

        :param ctx: The module context.
        :param repo: The quote repository for persistence.
        """
        self._ctx = ctx
        self._repo = repo

    async def add_quote(self, text: str, added_by: str) -> Quote:
        """Add a new quote.

        :param text: The quote text.
        :param added_by: Display name of the user who added the quote.
        :return: Snapshot of the newly created quote.
        """
        quote = await self._repo.create(text, added_by)
        self._ctx.logger.info("Quote #%d added by %s.", quote.id, added_by)
        return quote

    async def delete_quote(self, quote_id: int) -> Quote:
        """Delete a quote by ID.

        :param quote_id: Database ID of the quote.
        :return: Snapshot of the deleted quote.
        :raises QuoteNotFoundError: If no quote matches the ID.
        """
        quote = await self._repo.delete(quote_id)
        self._ctx.logger.info("Quote #%d deleted.", quote_id)
        return quote

    async def get_quote(self, quote_id: int) -> Quote:
        """Fetch a quote by ID.

        :param quote_id: Database ID of the quote.
        :return: The matching Quote.
        :raises QuoteNotFoundError: If no quote matches the ID.
        """
        return await self._repo.get(quote_id)

    async def get_random_quote(self) -> Quote | None:
        """Fetch a random quote.

        :return: A random Quote, or None if no quotes exist.
        """
        return await self._repo.get_random()

    async def list_quotes(self) -> list[Quote]:
        """Return all quotes ordered by ID.

        :return: List of Quote snapshots.
        """
        return await self._repo.list_all()


def get_manager(ctx: ModuleContext) -> QuoteManager:
    """Return the QuoteManager stored in the module context's state.

    :param ctx: The module context.
    :return: The active QuoteManager.
    :raises RuntimeError: If the manager has not been initialized.
    """
    manager = ctx.state.get("manager")
    if not isinstance(manager, QuoteManager):
        raise RuntimeError("QuoteManager is not initialized.")
    return manager
