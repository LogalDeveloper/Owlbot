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

"""YAML-based configuration for Owlbot."""

import logging
import os
import secrets
from pathlib import Path
from typing import Any, TypeVar, overload

import yaml

logger = logging.getLogger("owlbot.config")

_UNSET = object()
_T = TypeVar("_T")


class Config:
    """Configuration manager for Owlbot."""

    def __init__(
        self,
        config_path: str | Path = "config.yaml",
        overrides: dict[str, Any] | None = None,
    ):
        """Initialize the configuration manager.

        :param config_path: Path to the YAML config file.
        :param overrides: CLI overrides (keys match property names).
        """
        self.config_path = Path(config_path)
        self._overrides: dict[str, Any] = overrides or {}
        self._data: dict[str, Any] = {}
        self._module_defaults: dict[str, dict[str, Any]] = {}
        self.load()

    @overload
    def _resolve(
        self,
        *,
        section: str,
        key: str,
        default: str,
        env: str | None = ...,
        cli: Any = ...,
    ) -> str: ...

    @overload
    def _resolve(
        self,
        *,
        section: str,
        key: str,
        default: _T,
        type_fn: type[_T],
        env: str | None = ...,
        cli: Any = ...,
    ) -> _T: ...

    @overload
    def _resolve(
        self,
        *,
        section: str,
        key: str,
        env: str | None = ...,
        cli: Any = ...,
    ) -> str | None: ...

    def _resolve(
        self,
        *,
        env: str | None = None,
        cli: Any = _UNSET,
        section: str,
        key: str,
        default: Any = _UNSET,
        type_fn: type = str,
    ) -> Any:
        """Resolve a setting through the priority chain.

        Priority order: CLI arg > env var > config file > default.

        When *default* is a ``str``, the resolved value is coerced to ``str``
        (the implicit *type_fn*).  For non-string types, pass both *default*
        and a matching *type_fn* (e.g. ``default=8081, type_fn=int``).  When
        *default* is omitted the value may be ``None``.

        :param env: Environment variable name to check.
        :param cli: CLI override value (_UNSET or None means not provided).
        :param section: Top-level config section (e.g. "owlbot").
        :param key: Key within the section.
        :param default: Default value if nothing else is set.
        :param type_fn: Callable to coerce the resolved value.
        :return: The resolved value.
        """
        if cli is not _UNSET and cli is not None:
            return type_fn(cli)
        if env:
            val = os.environ.get(env)
            if val is not None:
                return type_fn(val)
        val = self._data.get(section, {}).get(key)
        if val is not None:
            return type_fn(val)
        if default is not _UNSET:
            return default
        return None

    @property
    def webhook_secret(self) -> str:
        """Secret string for the webhook URL path.

        The webhook endpoint is always /webhook/<secret>. A cryptographically
        secure value is generated on first run if not explicitly configured.
        """
        return self._resolve(
            cli=self._overrides.get("webhook_secret"),
            env="OWLBOT_WEBHOOK_SECRET",
            section="owlbot",
            key="webhook_secret",
            default="",
        )

    @property
    def webhook_path(self) -> str:
        """URL path where Owncast sends webhooks.

        Always returns /webhook/<secret>. The secret is auto-generated
        on first run if not configured.
        """
        return f"/webhook/{self.webhook_secret}"

    @property
    def host(self) -> str:
        """Address to bind the webhook server."""
        return self._resolve(
            cli=self._overrides.get("host"),
            env="OWLBOT_HOST",
            section="owlbot",
            key="host",
            default="127.0.0.1",
        )

    @property
    def port(self) -> int:
        """Port for the webhook server."""
        return self._resolve(
            cli=self._overrides.get("port"),
            env="OWLBOT_PORT",
            section="owlbot",
            key="port",
            default=8081,
            type_fn=int,
        )

    @property
    def handler_timeout(self) -> float:
        """Max seconds to wait for handlers to complete."""
        return self._resolve(
            env="OWLBOT_HANDLER_TIMEOUT",
            section="owlbot",
            key="handler_timeout",
            default=30.0,
            type_fn=float,
        )

    @property
    def pool_size(self) -> int:
        """Maximum number of SQLite connections per module storage pool."""
        return max(
            1,
            self._resolve(
                env="OWLBOT_POOL_SIZE",
                section="owlbot",
                key="pool_size",
                default=4,
                type_fn=int,
            ),
        )

    @property
    def command_prefix(self) -> str:
        """Prefix character for chat commands."""
        return self._resolve(
            env="OWLBOT_COMMAND_PREFIX",
            section="owlbot",
            key="command_prefix",
            default="!",
        )

    @property
    def public_base_url(self) -> str:
        """Public base URL for Owlbot's web server.

        Used to construct URLs for module routes and the webhook endpoint.
        Falls back to ``owncast.url`` if not explicitly set.
        """
        configured = self._resolve(
            env="OWLBOT_PUBLIC_BASE_URL",
            section="owlbot",
            key="public_base_url",
        )
        if configured:
            return configured.rstrip("/")
        return self.owncast_url.rstrip("/")

    @property
    def storage_dir(self) -> Path:
        """Directory for module database files.

        Each module gets its own database file named '<module_name>.db'.
        Defaults to 'data/' in the working directory.
        """
        configured = self._resolve(
            cli=self._overrides.get("storage_dir"),
            env="OWLBOT_STORAGE_DIR",
            section="owlbot",
            key="storage_dir",
        )
        if configured:
            return Path(configured)
        return Path("data")

    @property
    def modules_dir(self) -> Path:
        """Directory containing user modules.

        Built-in modules are loaded from the package regardless of this setting.
        Defaults to 'modules/' in the working directory.
        """
        configured = self._resolve(
            cli=self._overrides.get("modules_dir"),
            env="OWLBOT_MODULES_DIR",
            section="owlbot",
            key="modules_dir",
        )
        if configured:
            return Path(configured)
        return Path("modules")

    @property
    def log_dir(self) -> Path | None:
        """Directory for the log file.

        When set, an ``owlbot.log`` file is written to this directory in
        addition to stdout.  Returns ``None`` when unset (stdout only).
        """
        configured = self._resolve(
            cli=self._overrides.get("log_dir"),
            env="OWLBOT_LOG_DIR",
            section="owlbot",
            key="log_dir",
        )
        if configured:
            return Path(configured)
        return None

    @property
    def owncast_url(self) -> str:
        """Base URL of the Owncast server."""
        return self._resolve(
            env="OWLBOT_OWNCAST_URL",
            section="owncast",
            key="url",
            default="",
        )

    @property
    def owncast_access_token(self) -> str:
        """API access token for Owncast."""
        return self._resolve(
            env="OWLBOT_OWNCAST_ACCESS_TOKEN",
            section="owncast",
            key="access_token",
            default="",
        )

    @property
    def admin_enabled(self) -> bool:
        """Whether the Owncast admin client is enabled."""
        env_val = os.environ.get("OWLBOT_OWNCAST_ADMIN_ENABLED")
        if env_val is not None:
            return env_val.lower() in ("1", "true", "yes")
        return bool(
            self._data.get("owncast", {}).get("admin", {}).get("enabled", False)
        )

    @property
    def admin_username(self) -> str:
        """Username for the Owncast admin API."""
        env_val = os.environ.get("OWLBOT_OWNCAST_ADMIN_USERNAME")
        if env_val is not None:
            return env_val
        return str(
            self._data.get("owncast", {}).get("admin", {}).get("username", "admin")
        )

    @property
    def admin_password(self) -> str:
        """Password for the Owncast admin API."""
        env_val = os.environ.get("OWLBOT_OWNCAST_ADMIN_PASSWORD")
        if env_val is not None:
            return env_val
        return str(
            self._data.get("owncast", {}).get("admin", {}).get("password", "abc123")
        )

    def load(self) -> None:
        """Load config from YAML, applying defaults for missing sections."""
        logger.debug("Loading configuration from: %s", self.config_path.absolute())
        if self.config_path.exists():
            try:
                with self.config_path.open() as f:
                    self._data = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                logger.error(f"Failed to parse config file: {e}")
                raise
            except OSError as e:
                logger.error(f"Failed to read config file: {e}")
                raise
            logger.info(f"Configuration loaded from: {self.config_path.absolute()}")
        else:
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        if not isinstance(self._data.get("owlbot"), dict):
            self._data["owlbot"] = {}
        if not isinstance(self._data.get("owncast"), dict):
            self._data["owncast"] = {}
        if not isinstance(self._data.get("modules"), dict):
            self._data["modules"] = {}

        if not self.owncast_url:
            raise ValueError(
                "owncast.url is required. "
                "Set it in config.yaml, or via the OWLBOT_OWNCAST_URL "
                "environment variable "
                "(e.g. https://stream.logal.dev)."
            )

        # Generate a cryptographically secure secret if one isn't configured
        # (and not provided via env var or CLI override).
        if not self.webhook_secret:
            generated = secrets.token_urlsafe(32)
            self._data["owlbot"]["webhook_secret"] = generated
            self.save()
            logger.info("Generated webhook secret and saved to config file.")

        logger.debug("Webhook Secret: [set]")
        logger.debug("Server Bind Address: %s:%s", self.host, self.port)
        logger.debug("Owncast Server URL: %s", self.owncast_url)
        logger.debug(
            "Owncast Access Token: %s",
            "[set]" if self.owncast_access_token else "[unset]",
        )
        logger.debug("Command Prefix: %r", self.command_prefix)
        logger.debug("Handler Timeout: %ss", self.handler_timeout)
        logger.debug("Storage Directory: %s", self.storage_dir)
        logger.debug("Modules Directory: %s", self.modules_dir)
        logger.debug("Log Directory: %s", self.log_dir or "[unset]")
        logger.debug("Public Base URL: %s", self.public_base_url)
        logger.debug(
            "Admin API Enabled: %s",
            "[set]" if self.admin_enabled else "[unset]",
        )
        if self.admin_enabled:
            logger.debug("Admin API Username: %s", self.admin_username)
            logger.debug(
                "Admin API Password: %s",
                "[set]" if self.admin_password else "[unset]",
            )

    def save(self) -> None:
        """Write current configuration to the YAML file."""
        logger.debug("Saving configuration to: %s", self.config_path.absolute())
        try:
            with self.config_path.open("w") as f:
                yaml.safe_dump(
                    self._ordered_data(), f, default_flow_style=False, sort_keys=False
                )
        except yaml.YAMLError as e:
            logger.error(f"Failed to serialize config data: {e}")
            raise
        except OSError as e:
            logger.error(f"Failed to write config file: {e}")
            raise
        logger.info(f"Configuration saved to: {self.config_path.absolute()}")

    def is_module_enabled(self, module_name: str) -> bool:
        """Check if a module is enabled.

        Modules are enabled by default unless explicitly disabled with
        ``modules.<name>.enabled: false`` in the config file.

        :param module_name: The module name (filename without .py extension).
        :return: True if enabled, False if disabled.
        """
        module_config = self._data.get("modules", {}).get(module_name, {})

        if isinstance(module_config, dict):
            return bool(module_config.get("enabled", True))

        # Module might be configured as a scalar or missing entirely.
        # Default to enabled when the value is not a mapping.
        return True

    def get_module_config(self, module_name: str) -> dict[str, Any]:
        """Get the configuration dict for a module.

        Returns merged defaults and config file values, with config file
        values taking precedence.

        :param module_name: The module name.
        :return: Dict of configuration values for the module.
        """
        defaults = self._module_defaults.get(module_name, {})
        config = self._data.get("modules", {}).get(module_name, {})

        if not isinstance(config, dict):
            config = {}

        # Config file values take precedence over module defaults.
        return {**defaults, **config}

    def set_module_config(self, module_name: str, config: dict[str, Any]) -> None:
        """Update the configuration for a module at runtime and persist to disk.

        :param module_name: The module name.
        :param config: Dict of configuration values to set.
        """
        if not isinstance(self._data.get("modules"), dict):
            self._data["modules"] = {}
        self._data["modules"][module_name] = config
        self.save()
        module_logger = logging.getLogger(f"owlbot.modules.{module_name}.config")
        module_logger.debug("Updated config.")

    def register_module_defaults(
        self, module_name: str, defaults: dict[str, Any]
    ) -> None:
        """Register default configuration values for a module.

        Called by modules during setup to declare their expected config keys
        and default values. Missing keys are backfilled into the config file
        so the YAML always reflects all available options.

        :param module_name: The module name.
        :param defaults: Dict of default configuration values.
        """
        self._module_defaults[module_name] = defaults

        if not isinstance(self._data.get("modules"), dict):
            self._data["modules"] = {}

        existing = self._data["modules"].get(module_name, {})

        if not isinstance(existing, dict):
            existing = {}

        changed = False
        merged = {**defaults, **existing}

        if module_name not in self._data["modules"]:
            # New module section. Include enabled: True alongside all defaults.
            merged.setdefault("enabled", True)
            changed = True
        elif len(merged) > len(existing):
            # Existing section, but some default keys were missing.
            changed = True

        self._data["modules"][module_name] = merged

        if changed:
            self.save()

        module_logger = logging.getLogger(f"owlbot.modules.{module_name}.config")
        module_logger.debug("Registered defaults.")

    def _ordered_data(self) -> dict[str, Any]:
        """Return config data with sections in a stable order."""
        ordered: dict[str, Any] = {}
        for key in ("owlbot", "owncast", "modules"):
            if key in self._data:
                value = self._data[key]
                if key == "modules" and isinstance(value, dict):
                    value = dict(sorted(value.items()))
                ordered[key] = value
        for key in sorted(self._data.keys() - ordered.keys()):
            ordered[key] = self._data[key]
        return ordered


class ModuleConfig:
    """Pre-scoped configuration for a specific module."""

    def __init__(self, config: Config, module_name: str):
        """Initialize a module-scoped configuration.

        :param config: The parent Config object.
        :param module_name: The name of the module this config
            is scoped to (internal use).
        """
        self._config = config
        self._module_name = module_name

    @property
    def enabled(self) -> bool:
        """Check if this module is enabled in config."""
        return self._config.is_module_enabled(self._module_name)

    @property
    def public_base_url(self) -> str:
        """Public base URL for Owlbot's web server."""
        return self._config.public_base_url

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by key.

        :param key: The configuration key.
        :param default: Value to return if key is not found.
        :return: The config value, or default if not found.
        """
        return self.as_dict().get(key, default)

    def as_dict(self) -> dict[str, Any]:
        """Get the full config dict for this module.

        :return: Dict of all configuration values.
        """
        return self._config.get_module_config(self._module_name)

    def set(self, key: str, value: Any) -> None:
        """Set a config value at runtime and persist to disk.

        :param key: The configuration key.
        :param value: The value to set.
        """
        current = self._config.get_module_config(self._module_name)
        current[key] = value
        self._config.set_module_config(self._module_name, current)

    def register_defaults(self, defaults: dict[str, Any]) -> None:
        """Register default values for this module's config.

        Called during setup() to declare expected config keys and their
        default values.

        :param defaults: Dict of default configuration values.
        """
        self._config.register_module_defaults(self._module_name, defaults)
