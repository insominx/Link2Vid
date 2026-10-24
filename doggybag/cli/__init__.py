"""Headless, agent-first command line for Doggybag (see docs/headless-usage.md)."""

COMMANDS = ("info", "download", "transcript", "transcribe", "doctor", "schema")


def run_cli(argv, **kwargs) -> int:
    from .main import run_cli as _run_cli

    return _run_cli(argv, **kwargs)


__all__ = ["COMMANDS", "run_cli"]
