"""Tests for the VM lifecycle control service."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_CONTROL_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "proxy_control.py"
)
_spec = importlib.util.spec_from_file_location("proxy_control", _CONTROL_PATH)
assert _spec and _spec.loader
control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(control)


def test_all_lifecycle_commands_are_defined():
    assert set(control.COMMANDS) >= {"start", "stop", "reboot", "shutdown"}
    assert control.COMMANDS["start"] == ["up", "-d"]
    assert control.COMMANDS["stop"] == ["stop"]
    assert control.COMMANDS["reboot"] == ["up", "-d", "--force-recreate"]
    assert control.COMMANDS["shutdown"] == ["down", "--remove-orphans"]


def test_command_runs_docker_compose_with_the_selected_args(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, *, timeout=180, **_kwargs):
        calls.append((args, timeout))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(control, "_BASE", ["docker", "compose", "-f", "stack.yml"])
    monkeypatch.setattr(control.subprocess, "run", fake_run)
    monkeypatch.setattr(control, "_status", lambda: {"ok": True})

    result = control._command("reboot")

    assert result["ok"] is True
    assert calls[0][0] == [
        "docker",
        "compose",
        "-f",
        "stack.yml",
        "up",
        "-d",
        "--force-recreate",
    ]


def test_status_parses_compose_json(monkeypatch):
    monkeypatch.setattr(
        control,
        "_run",
        lambda _args, timeout=30: (
            0,
            '{"Name":"core","State":"running"}\n{"Name":"facade","State":"running"}',
        ),
    )
    status = control._status()
    assert status["ok"] is True
    assert status["containers"] == [
        {"Name": "core", "State": "running"},
        {"Name": "facade", "State": "running"},
    ]
