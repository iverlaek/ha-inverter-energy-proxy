"""Command-line entry point: ``python -m proxy``.

Usage::

    python -m proxy --config /config/inverter_proxy.yaml   # run (mode from config)
    python -m proxy --mode core                            # override the mode
    python -m proxy --once                                 # poll once and print status
    python -m proxy --check                                # validate the config and exit
    python -m proxy --print-config                         # print the effective config
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from dataclasses import asdict

from .config import MODES, Config
from .core import build_service
from .version import VERSION


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proxy", description=__doc__)
    parser.add_argument("--config", default=None, help="path to the config file")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=None,
        help="override the configured mode (all | core | facade)",
    )
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument("--check", action="store_true", help="validate config and exit")
    parser.add_argument(
        "--print-config", action="store_true", help="print the effective config"
    )
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = Config.load(args.config)
    if args.mode:
        config.mode = args.mode
    _configure_logging(config.log_level)

    if args.print_config:
        print(json.dumps(asdict(config), indent=2, default=str))
        return 0

    service = build_service(config)
    if args.check:
        print(f"configuration OK ({config.mode}): {service.describe()}")
        return 0
    if args.once:
        poll_once = getattr(service, "poll_once", None)
        if poll_once is not None:
            poll_once()
        print(json.dumps(service.status(), indent=2, default=str))
        return 0

    try:
        asyncio.run(_run(service))
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    return 0


async def _run(service) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, service.request_stop)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass
    await service.run()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
