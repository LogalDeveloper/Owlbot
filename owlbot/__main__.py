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

"""Entry point for running Owlbot as a module."""

from __future__ import annotations

import argparse
import asyncio
import importlib.resources
import logging
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

import uvloop

from . import __version__
from .api.config import Config
from .bot import Owlbot, StartupError


def _init() -> None:
    """Scaffold default config into the current directory."""
    config_dest = Path.cwd() / "config.yaml"
    if config_dest.exists():
        print(f"Skipped (already exists): {config_dest}")
        return

    # Try the installed package first, fall back to repo root for dev installs.
    source = importlib.resources.files("owlbot._defaults") / "config.example.yaml"
    if not source.is_file():
        source = Path(__file__).resolve().parent.parent / "config.example.yaml"
    if not source.is_file():
        print(
            "Error: Cannot locate default config. "
            "Ensure the package is installed correctly.",
            file=sys.stderr,
        )
        sys.exit(1)

    if isinstance(source, Path):
        shutil.copy2(source, config_dest)
    else:
        config_dest.write_bytes(source.read_bytes())
    print(f"Created: {config_dest}")


def main() -> None:
    """Parse command-line arguments and start the bot."""
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        _init()
        return

    parser = argparse.ArgumentParser(
        description="Owlbot - Owncast Chat Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  owlbot init                         # Scaffold default config

Examples:
  python -m owlbot                    # Run with info logging (default)
  python -m owlbot -v                 # Run with debug logging
  python -m owlbot -c my_config.yaml  # Use custom config file
        """,
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "-m",
        "--modules",
        default=None,
        help="Path to user modules directory (default: modules)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Address to bind the web server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for the web server",
    )
    parser.add_argument(
        "-s",
        "--storage-dir",
        default=None,
        help="Directory for module database files",
    )
    parser.add_argument(
        "-l",
        "--log-dir",
        default=None,
        help="Directory for the owlbot.log file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    parser.add_argument(
        "--skip-api-check",
        action="store_true",
        help="Skip API accessibility checks during startup",
    )
    parser.add_argument(
        "--webhook-path",
        action="store_true",
        help="Print the webhook path from config and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Owlbot {__version__}",
    )

    args = parser.parse_args()

    overrides: dict[str, Any] = {}
    if args.host is not None:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = args.port
    if args.modules is not None:
        overrides["modules_dir"] = args.modules
    if args.storage_dir is not None:
        overrides["storage_dir"] = args.storage_dir
    if args.log_dir is not None:
        overrides["log_dir"] = args.log_dir

    if args.webhook_path:
        try:
            config = Config(args.config, overrides=overrides)
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            sys.exit(1)
        if config._data.get("owlbot", {}).get("public_base_url"):
            base = config.public_base_url
        else:
            base = f"http://{config.host}:{config.port}"
        print(f"{base}{config.webhook_path}")
        return

    log_level = logging.DEBUG if args.verbose else logging.INFO

    if args.verbose:
        log_format = (
            "%(asctime)s [%(levelname)s] %(name)s "
            "(%(filename)s:%(lineno)d): %(message)s"
        )
    else:
        log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Suppress aiohttp's access logs unless we're in verbose mode.
    # These logs are very noisy and not useful for normal operation.
    if not args.verbose:
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    logger = logging.getLogger("owlbot")

    logger.info(f"Log level: {logging.getLevelName(log_level)}")

    try:
        bot = Owlbot(
            config_path=args.config,
            overrides=overrides,
            skip_api_check=args.skip_api_check,
        )
    except StartupError as e:
        logger.error(str(e))
        sys.exit(1)

    if bot.config.log_dir is not None:
        try:
            bot.config.log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(
                bot.config.log_dir / "owlbot.log",
                mode="a",
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(
                logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S"),
            )
            logging.getLogger().addHandler(file_handler)
            logger.info(f"File logging enabled: {bot.config.log_dir / 'owlbot.log'}")
        except OSError as e:
            logger.warning(f"Could not set up file logging: {e}")

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        async with bot:
            await stop_event.wait()
            logger.info("Received shutdown signal.")

    try:
        uvloop.run(_run())
    except StartupError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception:
        logger.exception("Unexpected error during Owlbot execution.")
        sys.exit(1)


if __name__ == "__main__":
    main()
