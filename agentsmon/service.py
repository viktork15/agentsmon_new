"""Boot persistence — keep the dashboard + keepalive running across reboots.

We use **cron** (a launcher run `@reboot` and every minute) rather than systemd ``--user`` or a
macOS LaunchAgent. On a headless server reached over SSH there's often no user D-Bus / systemd
instance (``systemctl --user`` fails with "Failed to connect to bus: No medium found") and a
macOS LaunchAgent needs a GUI login session. A cron launcher that nohups the dashboard (guarded
by pgrep) and runs one keepalive pass works everywhere, no login session required.
"""
from __future__ import annotations

import os
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
# Guard on the dashboard's own pidfile (a plain `pgrep -f "agentsmon dashboard"` also matched any
# shell whose command line mentioned the dashboard, and then nothing was started — 2026-09-04).
PIDFILE="{state}/dashboard.pid"
if ! ( [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null ) \\
   && ! pgrep -f -- "-m agentsmon dashboard$" >/dev/null 2>&1; then
  nohup "$PY" -m agentsmon dashboard >> "{log}" 2>&1 &
fi
"$PY" -m agentsmon keepalive >> "{log}" 2>&1
""", encoding="utf-8")
    path.chmod(0o755)
    return path


def _dashboard_pids() -> list[int]:
    """The running dashboard's PID(s): the pidfile it writes on start, else a pattern that only
    matches the dashboard's own command line (``-m agentsmon dashboard``). A bare
    ``pkill -f "agentsmon dashboard"`` also killed any shell script whose text mentioned the
    dashboard — the very script driving the install (2026-09-04)."""
    from . import dashboard as _dash
    pids: list[int] = []
    try:
        pid = int(_dash.pidfile_path().read_text("utf-8").strip())
        os.kill(pid, 0)                              # still alive?
        pids.append(pid)
    except (OSError, ValueError):
        pass
    if not pids:
        out = subprocess.run(["pgrep", "-f", r"-m agentsmon dashboard$"], capture_output=True, text=True).stdout
        pids = [int(x) for x in out.split() if x.isdigit() and int(x) != os.getpid()]
    return pids


def stop_dashboard(wait: float = 8.0) -> None:
    """Stop the running dashboard and WAIT until it is gone (port freed) before relaunching.
    Otherwise the launcher's guard still sees the dying process, or the new one hits "address
    already in use" — either way the STALE pre-config dashboard keeps serving."""
    import signal
    pids = _dashboard_pids()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + wait
    while pids and time.time() < deadline:
        pids = [p for p in pids if _alive(p)]
        if pids and time.time() > deadline - wait / 2:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)  # stubborn → escalate
                except OSError:
                    pass
        time.sleep(0.3)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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
    # would just leave the stale one bound to the old address. (Safe: our own process is
    # "agentsmon setup/service", not "agentsmon dashboard".)
    stop_dashboard()
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
