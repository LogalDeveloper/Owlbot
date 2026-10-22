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

"""Integration test fixtures and harness for the Owncast API clients."""

from __future__ import annotations

import asyncio
import hashlib
import platform
import secrets
import shutil
import socket
import subprocess
import tempfile
import urllib.request
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

import pytest
import pytest_asyncio
import uvloop

from owlbot import OWNCAST_TARGET_VERSION
from owlbot.api.http_client import HttpClient
from owlbot.api.owncast_admin_client import OwncastAdminClient
from owlbot.api.owncast_client import OwncastClient
from owlbot.owncast_http import OwncastError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

_RELEASE_URL_TEMPLATE: Final = (
    "https://github.com/owncast/owncast/releases/download/"
    "v{version}/owncast-{version}-{archive}.zip"
)

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_CACHE_DIR: Final = _REPO_ROOT / ".owncast-test"

_DEFAULT_ADMIN_USERNAME: Final = "admin"
_DEFAULT_ADMIN_PASSWORD: Final = "abc123"  # noqa: S105  # documented Owncast default

_INTEGRATION_SCOPES: Final[list[str]] = [
    "CAN_SEND_SYSTEM_MESSAGES",
    "CAN_SEND_MESSAGES",
    "HAS_ADMIN_ACCESS",
]


class _PlatformRelease(NamedTuple):
    """Archive name and expected SHA256 for a single release platform."""

    archive: str
    sha256: str


_PLATFORM_RELEASES: Final[dict[tuple[str, str], _PlatformRelease]] = {
    ("Linux", "x86_64"): _PlatformRelease(
        "linux-64bit",
        "8753c12ce58b7d1120c815e49d19f8fd3cc1a53dc8e0abb581e212694ca670d8",
    ),
    ("Linux", "i686"): _PlatformRelease(
        "linux-32bit",
        "f4bc74b91ecc82a17cb12d18666199efab22151f48854ecaeb79d467ccdd18fb",
    ),
    ("Linux", "aarch64"): _PlatformRelease(
        "linux-arm64",
        "81dddbe81122fb998a071035f50aa3f6cbfc0a39d72a7a37ecb80118846b4256",
    ),
    ("Linux", "armv7l"): _PlatformRelease(
        "linux-arm7",
        "d88a095d6424a97642b2f3c0188127bfb3cd36b0f46d0dafee36a496514f7234",
    ),
    ("Darwin", "x86_64"): _PlatformRelease(
        "macOS-64bit",
        "66f603fcea7ba85a6bd2848567d11149c267ea78828b037a914059fad7f88af4",
    ),
    ("Darwin", "arm64"): _PlatformRelease(
        "macOS-arm64",
        "3d4dcca63d448bd967d4a92ad144922ff678820e97933f88f5808c98cb82e7e7",
    ),
}


@dataclass(slots=True)
class OwncastServer:
    """Running Owncast subprocess bound to chosen HTTP and RTMP ports."""

    binary: Path
    tmp_dir: Path
    http_port: int
    rtmp_port: int
    process: subprocess.Popen[bytes] | None = None

    @property
    def base_url(self) -> str:
        """Base URL for the running server (no trailing slash)."""
        return f"http://127.0.0.1:{self.http_port}"

    def start(self, startup_timeout: float = 30.0) -> None:
        """Launch Owncast and poll until ``/api/status`` responds."""
        self.process = subprocess.Popen(  # noqa: S603  # binary path is a cached, hash-verified Owncast release
            [
                str(self.binary),
                "-webserverport",
                str(self.http_port),
                "-rtmpport",
                str(self.rtmp_port),
            ],
            cwd=self.tmp_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            uvloop.run(self._poll_until_ready(startup_timeout))
        except BaseException:
            self.stop()
            raise

    async def _poll_until_ready(self, startup_timeout: float) -> None:
        last_exc: OwncastError | None = None
        http = HttpClient()
        await http._start()
        try:
            client = OwncastClient(
                base_url=self.base_url, access_token="", http_client=http
            )
            async with asyncio.timeout(startup_timeout):
                while True:
                    if self.process is not None and self.process.poll() is not None:
                        msg = (
                            f"Owncast exited before becoming ready "
                            f"(code={self.process.returncode})."
                        )
                        raise RuntimeError(msg)
                    try:
                        await client.get_status()
                    except OwncastError as exc:
                        last_exc = exc
                    else:
                        return
                    await asyncio.sleep(0.25)
        except TimeoutError as exc:
            msg = (
                f"Owncast did not become ready within {startup_timeout}s. "
                f"Last error: {last_exc}."
            )
            raise RuntimeError(msg) from exc
        finally:
            await http._close()

    def stop(self, shutdown_timeout: float = 10.0) -> None:
        """Terminate the subprocess, escalating to SIGKILL if needed."""
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=shutdown_timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None


@dataclass(frozen=True, slots=True)
class AdminCredentials:
    """Admin credentials rotated at session start."""

    username: str
    password: str


def _cached_binary_path(release: _PlatformRelease) -> Path:
    """Return the path where an extracted binary for ``release`` lives."""
    return _CACHE_DIR / f"v{OWNCAST_TARGET_VERSION}" / release.archive / "owncast"


def _cached_archive_path(release: _PlatformRelease) -> Path:
    """Return the path where the downloaded archive for ``release`` is cached."""
    return _CACHE_DIR / f"v{OWNCAST_TARGET_VERSION}" / release.archive / "archive.zip"


@pytest.fixture(scope="session")
def owncast_binary() -> Path:
    """Path to a cached, verified Owncast binary for the current platform.

    On cache hit the pinned SHA256 is re-checked against the cached
    archive; on miss or mismatch the archive is re-downloaded, verified,
    and extracted. Raises ``RuntimeError`` on network failure, hash
    mismatch, or unsupported host platform.
    """
    key = (platform.system(), platform.machine())
    try:
        release = _PLATFORM_RELEASES[key]
    except KeyError as exc:
        supported = ", ".join(f"{s}/{m}" for s, m in _PLATFORM_RELEASES)
        msg = (
            f"Unsupported platform {key[0]}/{key[1]} for Owncast integration "
            f"tests. Supported: {supported}."
        )
        raise RuntimeError(msg) from exc

    archive_path = _cached_archive_path(release)
    binary_path = _cached_binary_path(release)
    # Re-hash on hit so bumping the pinned SHA256 forces a re-download
    # to pick up the new upstream archive.
    if (
        archive_path.is_file()
        and binary_path.is_file()
        and hashlib.sha256(archive_path.read_bytes()).hexdigest() == release.sha256
    ):
        return binary_path

    # Stream to a tempfile and hash while downloading, so a tampered or
    # truncated download can't overwrite a valid cached copy.
    url = _RELEASE_URL_TEMPLATE.format(
        version=OWNCAST_TARGET_VERSION, archive=release.archive
    )
    hasher = hashlib.sha256()
    with tempfile.NamedTemporaryFile(
        prefix=f"owncast-v{OWNCAST_TARGET_VERSION}-{release.archive}-",
        suffix=".zip",
        delete=False,
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)
        with urllib.request.urlopen(url) as response:  # noqa: S310  # hash-verified below
            while chunk := response.read(65536):
                tmp_file.write(chunk)
                hasher.update(chunk)
    try:
        actual_hash = hasher.hexdigest()
        if actual_hash != release.sha256:
            msg = (
                f"SHA256 mismatch for downloaded Owncast archive. "
                f"expected={release.sha256} actual={actual_hash}"
            )
            raise RuntimeError(msg)

        # Hash matched: wipe the extract dir (in case of partial prior
        # downloads), move the verified archive in, extract alongside.
        extract_root = _CACHE_DIR / f"v{OWNCAST_TARGET_VERSION}" / release.archive
        if extract_root.exists():
            shutil.rmtree(extract_root)
        extract_root.mkdir(parents=True)

        shutil.move(tmp_path, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_root)
        if not binary_path.exists():
            msg = f"Extracted archive did not contain owncast binary at {binary_path}."
            raise RuntimeError(msg)
        binary_path.chmod(0o755)
        return binary_path
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.fixture
def owncast_server(
    owncast_binary: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    """Per-test Owncast server on two kernel-assigned ports; yields base URL.

    Scoped per test so each test gets a fresh subprocess and a fresh
    working directory, eliminating cross-test config-state pollution.
    """
    tmp_dir = tmp_path_factory.mktemp("owncast")
    # Bind both sockets before closing either so the kernel doesn't
    # hand out the same port twice.
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as http_sock,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rtmp_sock,
    ):
        for sock in (http_sock, rtmp_sock):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
        http_port = http_sock.getsockname()[1]
        rtmp_port = rtmp_sock.getsockname()[1]
    server = OwncastServer(
        binary=owncast_binary,
        tmp_dir=tmp_dir,
        http_port=http_port,
        rtmp_port=rtmp_port,
    )
    server.start()
    try:
        yield server.base_url
    finally:
        server.stop()


@asynccontextmanager
async def _admin_session(
    base_url: str, username: str, password: str
) -> AsyncIterator[OwncastAdminClient]:
    """Yield a short-lived ``OwncastAdminClient`` with a managed HTTP session."""
    http = HttpClient()
    await http._start()
    try:
        yield OwncastAdminClient(base_url, username, password, http)
    finally:
        await http._close()


@pytest.fixture
def admin_credentials(owncast_server: str) -> AdminCredentials:
    """Rotate the default admin password via the real admin client."""
    new_password = secrets.token_urlsafe(32)

    async def _rotate() -> None:
        async with _admin_session(
            owncast_server, _DEFAULT_ADMIN_USERNAME, _DEFAULT_ADMIN_PASSWORD
        ) as client:
            await client.set_admin_password(new_password)

    uvloop.run(_rotate())
    return AdminCredentials(username=_DEFAULT_ADMIN_USERNAME, password=new_password)


@pytest.fixture
def integration_token(owncast_server: str, admin_credentials: AdminCredentials) -> str:
    """Mint an integration access token via the real admin client.

    ``create_access_token`` on the client returns only the envelope message,
    so the token value is retrieved with a follow-up ``get_access_tokens``
    listing (matched by ``displayName``).
    """
    token_name = f"owlbot-integration-tests-{secrets.token_hex(4)}"

    async def _mint() -> str:
        async with _admin_session(
            owncast_server, admin_credentials.username, admin_credentials.password
        ) as client:
            await client.create_access_token(token_name, _INTEGRATION_SCOPES)
            tokens = await client.get_access_tokens()
        for entry in tokens:
            if isinstance(entry, dict) and entry.get("displayName") == token_name:
                return str(entry["accessToken"])
        msg = f"Newly-created token {token_name!r} not found in listing."
        raise RuntimeError(msg)

    return uvloop.run(_mint())


@pytest_asyncio.fixture
async def owncast_http() -> AsyncIterator[HttpClient]:
    """Fresh ``HttpClient`` per test, started and torn down cleanly."""
    client = HttpClient()
    await client._start()
    try:
        yield client
    finally:
        await client._close()


@pytest.fixture
def owncast_admin_client(
    owncast_server: str,
    admin_credentials: AdminCredentials,
    owncast_http: HttpClient,
) -> OwncastAdminClient:
    """Real ``OwncastAdminClient`` bound to the live server and current creds."""
    return OwncastAdminClient(
        owncast_server,
        admin_credentials.username,
        admin_credentials.password,
        owncast_http,
    )


@pytest.fixture
def owncast_client(
    owncast_server: str,
    integration_token: str,
    owncast_http: HttpClient,
) -> OwncastClient:
    """Real ``OwncastClient`` bound to the live server and bootstrap token."""
    return OwncastClient(
        owncast_server,
        integration_token,
        owncast_http,
    )
