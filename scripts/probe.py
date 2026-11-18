#!/usr/bin/env python3
"""Probe a GoodWe facade (or a real dongle) with the proxy's own transport.

Usage::

    scripts/probe.py [host] [--offset 47515] [--count 16] [--port 8899]

With no host it uses GOODWE_FACADE_IP (then CORE_IP) from ``.env``, falling back
to localhost.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proxy.transport.goodwe_udp import GoodweUdpClient  # noqa: E402


def _from_env(name: str) -> str:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", default=None)
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--offset", type=int, default=47515)
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--addr", type=lambda v: int(v, 0), default=0xF7)
    args = parser.parse_args()

    host = args.host or _from_env("GOODWE_FACADE_IP") or _from_env("CORE_IP") or "127.0.0.1"
    client = GoodweUdpClient(host, port=args.port, comm_addr=args.addr)
    registers = client.read_registers(args.offset, args.count)
    print(f"{host}:{args.port} @ {args.offset} x{args.count}")
    for index, value in enumerate(registers):
        print(f"  {args.offset + index:>6}  0x{value:04X}  {value:>6}  {value - 0x10000 if value >= 0x8000 else value:>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
