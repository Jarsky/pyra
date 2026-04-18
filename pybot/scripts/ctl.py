"""pybot-ctl — daemon process manager."""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PID_FILE = Path("data/pyra.pid")
DEFAULT_CONFIG = Path("config/config.yaml")
DEFAULT_LOG = Path("data/pyra.log")
DEFAULT_PARTYLINE_HOST = "127.0.0.1"
DEFAULT_PARTYLINE_PORT = 3333


def _pid_file() -> Path:
    return Path(os.environ.get("PYRA_PID_FILE", DEFAULT_PID_FILE))


def _config_file() -> Path:
    return Path(os.environ.get("PYRA_CONFIG", DEFAULT_CONFIG))


def _log_file() -> Path:
    return Path(os.environ.get("PYRA_LOG", DEFAULT_LOG))


def _read_pid() -> int | None:
    pid_path = _pid_file()
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return None


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _cmd_start(args: argparse.Namespace) -> int:
    pid = _read_pid()
    if pid and _is_running(pid):
        print(f"Pyra is already running (PID {pid}).")
        return 1

    config = args.config or _config_file()
    if not Path(config).exists():
        print(f"Config not found: {config}")
        print("Run 'pybot-setup' first.")
        return 1

    pid_path = _pid_file()
    pid_path.parent.mkdir(parents=True, exist_ok=True)

    log_path = _log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-m", "pybot", "--config", str(config)]
    if args.debug:
        cmd.append("--debug")

    with open(log_path, "a") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
        )

    pid_path.write_text(str(proc.pid))
    print(f"Pyra started (PID {proc.pid}).")
    print(f"Logs: {log_path}")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    pid = _read_pid()
    if not pid:
        print("Pyra is not running (no PID file).")
        return 1
    if not _is_running(pid):
        print(f"Pyra is not running (stale PID {pid}).")
        _pid_file().unlink(missing_ok=True)
        return 1

    print(f"Stopping Pyra (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    timeout = args.timeout if hasattr(args, "timeout") else 10
    for _ in range(timeout * 10):
        if not _is_running(pid):
            break
        time.sleep(0.1)
    else:
        print(f"Process did not stop after {timeout}s, sending SIGKILL.")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    _pid_file().unlink(missing_ok=True)
    print("Pyra stopped.")
    return 0


def _cmd_restart(args: argparse.Namespace) -> int:
    _cmd_stop(args)
    time.sleep(1)
    return _cmd_start(args)


def _cmd_status(args: argparse.Namespace) -> int:
    pid = _read_pid()
    if not pid:
        print("Pyra: stopped (no PID file)")
        return 1
    if _is_running(pid):
        print(f"Pyra: running (PID {pid})")
        return 0
    else:
        print(f"Pyra: stopped (stale PID {pid})")
        return 1


def _cmd_reload(args: argparse.Namespace) -> int:
    pid = _read_pid()
    if not pid or not _is_running(pid):
        print("Pyra is not running.")
        return 1
    os.kill(pid, signal.SIGHUP)
    print(f"Sent SIGHUP to PID {pid} (plugin reload).")
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    log_path = _log_file()
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        return 1

    lines = args.lines if hasattr(args, "lines") else 50
    follow = args.follow if hasattr(args, "follow") else False

    if follow:
        cmd = ["tail", f"-n{lines}", "-f", str(log_path)]
    else:
        cmd = ["tail", f"-n{lines}", str(log_path)]

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_console(args: argparse.Namespace) -> int:
    host = args.host or DEFAULT_PARTYLINE_HOST
    port = args.port or DEFAULT_PARTYLINE_PORT

    nc = shutil.which("nc") or shutil.which("ncat") or shutil.which("netcat")
    telnet = shutil.which("telnet")

    if nc:
        print(f"Connecting to partyline at {host}:{port} (nc)...")
        os.execlp(nc, nc, host, str(port))  # noqa: S606
    elif telnet:
        print(f"Connecting to partyline at {host}:{port} (telnet)...")
        os.execlp(telnet, telnet, host, str(port))  # noqa: S606
    else:
        print(f"Connect manually: telnet {host} {port}")
        print("(Install 'netcat' or 'telnet' for automatic connection)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pybot-ctl",
        description="Pyra IRC Bot daemon manager",
    )
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="Start the bot daemon")
    sub.add_parser("stop", help="Stop the bot daemon")
    sub.add_parser("restart", help="Restart the bot daemon")
    sub.add_parser("status", help="Show daemon status")
    sub.add_parser("reload", help="Reload plugins (SIGHUP)")

    logs_p = sub.add_parser("logs", help="Show bot log output")
    logs_p.add_argument("-n", "--lines", type=int, default=50, help="Number of lines")
    logs_p.add_argument("-f", "--follow", action="store_true", help="Follow log output")

    console_p = sub.add_parser("console", help="Connect to partyline console")
    console_p.add_argument("--host", default=DEFAULT_PARTYLINE_HOST)
    console_p.add_argument("--port", type=int, default=DEFAULT_PARTYLINE_PORT)

    args = parser.parse_args()

    handlers = {
        "start": _cmd_start,
        "stop": _cmd_stop,
        "restart": _cmd_restart,
        "status": _cmd_status,
        "reload": _cmd_reload,
        "logs": _cmd_logs,
        "console": _cmd_console,
    }

    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
