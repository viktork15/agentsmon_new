#!/usr/bin/env bash
# Agents Monitoring one-command installer.
# Usage:  curl -fsSL <raw-url>/install.sh | bash      (or run it from a clone)
set -euo pipefail
cd "$PWD" 2>/dev/null || cd "$HOME" 2>/dev/null || cd /

REPO="https://github.com/petrludwig-collab/AgentsMonitoring.git"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
err() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# Where to drop the launcher: prefer a writable $HOME dir ALREADY on PATH so `agentsmon` works in
# this very terminal right away (a piped installer can't change the parent shell's PATH). Else
# ~/.local/bin, added to the rc files for new shells.
pick_bindir() {
  oldifs="$IFS"; IFS=:
  for d in $PATH; do
    case "$d" in
      "$HOME"/*) if [ -d "$d" ] && [ -w "$d" ]; then IFS="$oldifs"; printf '%s' "$d"; return; fi ;;
    esac
  done
  IFS="$oldifs"; printf '%s' "$HOME/.local/bin"
}

PY="$(command -v python3 || true)"
[ -n "$PY" ] || err "python3 not found. Install Python 3.10+ first."
"$PY" - <<'PYEOF' || err "Python 3.10+ required."
import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)
PYEOF
say "Using $("$PY" --version)"
command -v tmux >/dev/null || say "note: tmux not found — agents run in tmux, install it before setup."

# Get the code (clone unless already inside it).
if [ -f pyproject.toml ] && grep -q "agents-monitoring" pyproject.toml 2>/dev/null; then
  SRC="$(pwd)"; say "Installing from current directory"
else
  command -v git >/dev/null || err "git not found."
  SRC="${HOME}/.agentsmon-src"
  if [ -d "$SRC/.git" ]; then say "Updating $SRC"; git -C "$SRC" pull --ff-only
  else say "Cloning into $SRC"; git clone --depth 1 "$REPO" "$SRC"; fi
fi

# pip is OPTIONAL — the package is pure standard library. Try pip (so hooks/other tools can
# import it), but EITHER WAY drop our own launcher into a dir on PATH so `agentsmon` is a real
# command right after install — no PYTHONPATH to remember, no new shell when a PATH dir is writable.
if "$PY" -m pip --version >/dev/null 2>&1 || "$PY" -m ensurepip --upgrade >/dev/null 2>&1; then
  "$PY" -m pip install --user --upgrade "$SRC" >/dev/null 2>&1 \
    || "$PY" -m pip install --user --break-system-packages --upgrade "$SRC" >/dev/null 2>&1 || true
fi
BIND="$(pick_bindir)"
mkdir -p "$BIND"
printf '#!/bin/sh\nexec env PYTHONPATH="%s" "%s" -m agentsmon "$@"\n' "$SRC" "$PY" > "$BIND/agentsmon"
chmod +x "$BIND/agentsmon"
RUN=("$BIND/agentsmon"); HOW="agentsmon"
case ":$PATH:" in
  *":$BIND:"*)
    say "Installed launcher in $BIND (already on PATH) — 'agentsmon' works now." ;;
  *)
    say "Installed launcher in $BIND — adding it to PATH for new shells."
    for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.zshrc"; do
      [ -e "$rc" ] || continue
      grep -qs "$BIND" "$rc" || echo "export PATH=\"$BIND:\$PATH\"" >> "$rc"
    done
    grep -qs "$BIND" "$HOME/.bashrc" 2>/dev/null || echo "export PATH=\"$BIND:\$PATH\"" >> "$HOME/.bashrc"
    export PATH="$BIND:$PATH"
    say "For THIS terminal, run:  export PATH=\"$BIND:\$PATH\"   (new terminals get it automatically)" ;;
esac

say "Run it later with:  $HOW status   (add more bots anytime with:  $HOW add)"
if [ -e /dev/tty ]; then say "Starting setup…"; exec "${RUN[@]}" setup </dev/tty
else say "Installed. Finish setup with:  $HOW setup"; fi
