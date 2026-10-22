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

"""Request-contract regression tests for Owncast v0.3.0 admin endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiohttp
import pytest

from owlbot.api.owncast_admin_client import OwncastAdminClient
from owlbot.owncast_http import OwncastError

if TYPE_CHECKING:
    from aioresponses import aioresponses

    from owlbot.api.http_client import HttpClient

BASE_URL = "https://owncast.example.test"


@pytest.fixture
def admin_client(http: HttpClient) -> OwncastAdminClient:
    """Build a real admin client with the mocked HTTP transport."""
    return OwncastAdminClient(BASE_URL, "admin", "test-password", http)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get_playback_clients", "/api/admin/viewers/playback", [{"id": "player"}]),
        (
            "get_directory_followers",
            "/api/admin/followers/directory",
            [{"name": "directory"}],
        ),
        (
            "get_federated_servers",
            "/api/admin/federation/servers",
            {"servers": [{"id": 42}]},
        ),
        (
            "get_feature_requests",
            "/api/admin/federation/feature-requests",
            {"requests": [{"name": "directory"}]},
        ),
    ],
)
async def test_new_get_endpoints(
    admin_client: OwncastAdminClient,
    mocked_http: aioresponses,
    method: str,
    path: str,
    payload: Any,
) -> None:
    """New read endpoints preserve their response shapes and authenticate."""
    mocked_http.get(BASE_URL + path, payload=payload)
    assert await getattr(admin_client, method)() == payload
    request = next(iter(mocked_http.requests.values()))[0]
    assert request.kwargs["auth"] == aiohttp.BasicAuth("admin", "test-password")


async def test_get_users_passes_pagination_and_filters(
    admin_client: OwncastAdminClient, mocked_http: aioresponses
) -> None:
    """User queries carry every documented filter and return the pagination."""
    payload = {"total": 1, "results": [{"id": "user"}]}
    mocked_http.get(
        BASE_URL + "/api/admin/users"
        "?offset=10&limit=5&search=some+name&status=bots&sort=asc",
        payload=payload,
    )
    assert (
        await admin_client.get_users(
            10, 5, search="some name", status="bots", sort="asc"
        )
        == payload
    )


@pytest.mark.parametrize(
    ("method", "kwargs", "path", "body"),
    [
        (
            "delete_user",
            {"user_id": "user-123"},
            "/users/delete",
            {"userId": "user-123"},
        ),
        (
            "remove_follower",
            {"actor_iri": "https://example.test/actor"},
            "/followers/remove",
            {"actorIRI": "https://example.test/actor"},
        ),
        (
            "set_rtmp_bind_address",
            {"address": "127.0.0.1"},
            "/config/rtmpserverbindaddress",
            {"value": "127.0.0.1"},
        ),
        ("set_autoplay", {"mode": "sound-only"}, "/config/autoplay", {"value": "sound-only"}),
        (
            "set_federation_enable_quotes",
            {"enabled": True},
            "/config/federation/enablequotes",
            {"value": True},
        ),
        (
            "set_federation_hide_followers",
            {"hide": False},
            "/config/federation/hidefollowers",
            {"value": False},
        ),
        (
            "add_federated_server",
            {"url": "https://peer.example.test"},
            "/federation/servers",
            {"url": "https://peer.example.test"},
        ),
        (
            "set_favicon",
            {"base64_data_url": "data:image/png;base64,aW1hZ2U="},
            "/config/favicon",
            {"value": "data:image/png;base64,aW1hZ2U="},
        ),
    ],
)
async def test_post_request_contracts(
    admin_client: OwncastAdminClient,
    mocked_http: aioresponses,
    method: str,
    kwargs: dict[str, Any],
    path: str,
    body: dict[str, Any],
) -> None:
    """Mutation wrappers use exact v0.3.0 routes and JSON keys."""
    mocked_http.post(
        BASE_URL + "/api/admin" + path,
        payload={"success": True, "message": "updated"},
    )
    assert await getattr(admin_client, method)(**kwargs) == "updated"
    request = next(iter(mocked_http.requests.values()))[0]
    assert request.kwargs["json"] == body
    assert request.kwargs["auth"] == aiohttp.BasicAuth("admin", "test-password")
    assert request.kwargs.get("data") is None


async def test_remove_federated_server_uses_delete(
    admin_client: OwncastAdminClient, mocked_http: aioresponses
) -> None:
    """Federated server removal encodes the numeric ID in the DELETE path."""
    mocked_http.delete(
        BASE_URL + "/api/admin/federation/servers/42",
        payload={"success": True, "message": "removed"},
    )
    assert await admin_client.remove_federated_server(42) == "removed"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("disconnect_stream", "/api/admin/disconnect"),
        ("reset_yp_registration", "/api/admin/yp/reset"),
    ],
)
async def test_legacy_dict_mutations_use_post(
    admin_client: OwncastAdminClient,
    mocked_http: aioresponses,
    method: str,
    path: str,
) -> None:
    """Changed HTTP verbs retain the public response dictionary contract."""
    payload = {"success": True, "message": "done"}
    mocked_http.post(BASE_URL + path, payload=payload)
    assert await getattr(admin_client, method)() == payload


@pytest.mark.parametrize("secret", [None, "", "test-signing-secret"])
async def test_create_webhook_optional_secret(
    admin_client: OwncastAdminClient,
    mocked_http: aioresponses,
    secret: str | None,
) -> None:
    """Omit absent secrets while preserving explicitly empty or supplied ones."""
    mocked_http.post(
        BASE_URL + "/api/admin/webhooks/create",
        payload={"success": True, "message": "created"},
    )
    kwargs = {} if secret is None else {"secret": secret}
    assert (
        await admin_client.create_webhook(
            "https://example.test/hook", ["CHAT"], **kwargs
        )
        == "created"
    )
    request = next(iter(mocked_http.requests.values()))[0]
    expected: dict[str, Any] = {"url": "https://example.test/hook", "events": ["CHAT"]}
    if secret is not None:
        expected["secret"] = secret
    assert request.kwargs["json"] == expected


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("disconnect_stream", "/api/admin/disconnect"),
        ("reset_yp_registration", "/api/admin/yp/reset"),
    ],
)
async def test_post_mutations_propagate_errors(
    admin_client: OwncastAdminClient,
    mocked_http: aioresponses,
    method: str,
    path: str,
) -> None:
    """Do not synthesize a success dictionary for failed mutations."""
    mocked_http.post(
        BASE_URL + path, payload={"success": False, "message": "cannot update"}
    )
    with pytest.raises(OwncastError, match="cannot update"):
        await getattr(admin_client, method)()
