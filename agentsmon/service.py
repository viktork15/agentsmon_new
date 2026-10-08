"""Boot persistence — keep the dashboard + keepalive running across reboots.

We use **cron** (a launcher run `@reboot` and every minute) rather than systemd ``--user`` or a
macOS LaunchAgent. On a headless server reached over SSH there's often no user D-Bus / systemd
instance (``systemctl --user`` fails with "Failed to connect to bus: No medium found") and a
macOS LaunchAgent needs a GUI login session. A cron launcher that nohups the dashboard (guarded
by pgrep) and runs one keepalive pass works everywhere, no login session required.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import os
import signal

import agentsmon
from . import config

MARKER = "agentsmon-launch.sh"   # identifies our crontab lines


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _stop_dashboard() -> None:
    """Stop the running dashboard precisely via its PID file, then wait for the port to free.

    Falls back to a tightened pgrep/pkill match only when no usable PID file exists, so we don't
    rely on the broad ``-f "agentsmon dashboard"`` pattern that could match unrelated processes."""
    pid_path = config.state_dir() / "dashboard.pid"
    pid = None
    try:
        pid = int(pid_path.read_text("utf-8").strip())
    except (OSError, ValueError):
        pid = None

    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for i in range(25):
            if not _pid_alive(pid):
                break
            if i == 15:                       # stubborn → escalate to SIGKILL
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            time.sleep(0.3)
        try:
            pid_path.unlink()
        except OSError:
            pass
        return

    # No PID file (older install / never started): fall back to a precise command match.
    if shutil.which("pkill"):
        pat = "-m agentsmon dashboard"
        subprocess.run(["pkill", "-f", "--", pat], capture_output=True)
        for i in range(25):
            gone = subprocess.run(["pgrep", "-f", "--", pat], capture_output=True).returncode != 0
            if gone:
                break
            if i == 15:
                subprocess.run(["pkill", "-9", "-f", "--", pat], capture_output=True)
            time.sleep(0.3)


def _python() -> str:
    return sys.executable or "python3"


def _pythonpath() -> str:
    # Parent of the package dir, so the launcher imports agentsmon whether pip-installed or run
    # straight from a clone.
    return str(Path(agentsmon.__file__).resolve().parent.parent)


def _launcher_path() -> Path:
    return config.state_dir() / MARKER


def _write_launcher() -> Path:
    state = config.state_dir()
    log = state / "agentsmon.log"
    path = _launcher_path()
    path.write_text(f"""#!/bin/sh
# Agents Monitoring launcher — started by cron (@reboot + every minute). Idempotent: starts the
# dashboard only if it isn't running, then runs one keepalive pass (a no-op if disabled / no agents).
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="{_pythonpath()}"
export AGENTSMON_CONFIG="{config.DEFAULT_PATH}"
export AGENTSMON_STATE="{config.state_dir()}"
PY="{_python()}"
mkdir -p "{state}"
PIDFILE="{state}/dashboard.pid"
# Start the dashboard only if it isn't already running. Prefer the PID file (precise),
# fall back to a tightened command match so a missing PID file can't spawn a duplicate.
if {{ [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; }} || \\
   pgrep -f -- "-m agentsmon dashboard" >/dev/null 2>&1; then
  :
else
  nohup "$PY" -m agentsmon dashboard >> "{log}" 2>&1 &
fi
"$PY" -m agentsmon keepalive >> "{log}" 2>&1
""", encoding="utf-8")
    path.chmod(0o755)
    return path


def install() -> int:
    if not shutil.which("cron") and not shutil.which("crontab"):
        print("⚠️  crontab not found. Run these yourself under any process manager:")
        print(f"    {_python()} -m agentsmon dashboard &")
        print(f"    {_python()} -m agentsmon keepalive --loop &")
        return 1
    launcher = _write_launcher()
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except OSError:
        existing = ""
    lines = [ln for ln in existing.splitlines() if MARKER not in ln]
    lines.append(f"@reboot {launcher}")
    lines.append(f"* * * * * {launcher}")
    proc = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True,
                          capture_output=True)
    if proc.returncode != 0:
        print(f"✗ couldn't update crontab: {proc.stderr.strip()}")
        return 1
    # Stop any dashboard already running, so the launcher restarts it with the CURRENT config
    # (host/port/auth). Without this, a re-run can't change a live dashboard — its pgrep guard
    # would just leave the stale one bound to the old address.
    _stop_dashboard()
    # Kick it once now so the dashboard comes up immediately on the configured host.
    subprocess.run(["sh", str(launcher)], capture_output=True)
    print("  ✓ installed cron launcher (@reboot + every minute) — survives logout/reboot.")
    print(f"    launcher: {launcher}")
    print("    No systemd/launchd needed; works headless over SSH.")
    return 0


def uninstall_cron() -> None:
    """Remove our crontab lines (used by the uninstaller)."""
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except OSError:
        return
    kept = [ln for ln in existing.splitlines() if MARKER not in ln]
    subprocess.run(["crontab", "-"], input="\n".join(kept) + ("\n" if kept else ""), text=True,
                   capture_output=True)


def main() -> int:
    return install()
