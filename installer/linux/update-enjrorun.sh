#!/usr/bin/env bash
# Update Enjro Run on Linux: stop the server, reinstall from Git with uv, start it again.
#
#   installer/linux/update-enjrorun.sh                 # latest main
#   installer/linux/update-enjrorun.sh --release       # newest release tag (v0.1.9, ...)
#   installer/linux/update-enjrorun.sh --ref v0.1.8    # a tag, branch or commit (also rolls back)
#
# Models, settings and the API key live in ~/.enjro and are kept. Models that were loaded
# come back after the restart (unless restore_loaded is off).
#
# Upgrading from StrixRun (Enjro Run's old name) works too: the strixrun tool is replaced,
# ~/.strix/run moves to ~/.enjro/run and a strixrun.service user unit becomes enjrorun.service.

set -euo pipefail

SOURCE="${ENJRORUN_SOURCE:-${STRIXRUN_SOURCE:-git+ssh://git@github.com/keiththornton/strixrun}}"
REF=""
RELEASE=0
RESTART=1
UNIT="enjrorun.service"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--ref REF | --release] [--source URL] [--no-restart]

  --ref REF       install this tag, branch or commit (default: main)
  --release       install the newest release tag (v*)
  --source URL    where to install from (default: \$ENJRORUN_SOURCE or
                  $SOURCE)
                  e.g. git+https://github.com/keiththornton/strixrun once the repo is public
  --no-restart    leave Enjro Run stopped after updating
  -h, --help      show this help
EOF
}

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) [ $# -ge 2 ] || die "--ref needs a value"; REF="$2"; shift 2 ;;
    --ref=*) REF="${1#*=}"; shift ;;
    --release) RELEASE=1; shift ;;
    --source) [ $# -ge 2 ] || die "--source needs a value"; SOURCE="$2"; shift 2 ;;
    --source=*) SOURCE="${1#*=}"; shift ;;
    --no-restart) RESTART=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done
[ -n "$REF" ] && [ "$RELEASE" = 1 ] && die "use --ref or --release, not both"

# ---- prerequisites -----------------------------------------------------------------------

[ "$(uname -s)" = "Linux" ] || die "this updater is for Linux; on Windows run the new installer instead"
command -v uv >/dev/null || die "uv not found; install it from https://docs.astral.sh/uv/ (curl -LsSf https://astral.sh/uv/install.sh | sh)"
command -v git >/dev/null || die "git not found; install it with your package manager"

# The dashboard is built from source during the install. Without Node, uv would quietly
# build Enjro Run with no dashboard, so refuse instead.
command -v npm >/dev/null || die "npm not found; install Node 20 or newer (needed to build the dashboard)"
node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
[ "$node_major" -ge 20 ] 2>/dev/null || die "Node $node_major is too old; Enjro Run's dashboard needs Node 20 or newer"

if [ "$RELEASE" = 1 ]; then
  repo="${SOURCE#git+}"
  REF="$(git ls-remote --tags --refs "$repo" 'v*' | sed 's#.*refs/tags/##' | sort -V | tail -n1)"
  [ -n "$REF" ] || die "no release tags found at $repo"
  say "Newest release: $REF"
fi
TARGET="$SOURCE${REF:+@$REF}"

before=""
legacy=0
if command -v enjrorun >/dev/null; then
  before="$(enjrorun version 2>/dev/null || true)"
  say "Installed: Enjro Run ${before:-unknown version}"
elif command -v strixrun >/dev/null; then
  legacy=1
  before="$(strixrun version 2>/dev/null || true)"
  say "Installed: StrixRun ${before:-unknown version}; replacing it with Enjro Run"
else
  say "Enjro Run isn't installed yet; installing it"
fi

# ---- stop the running server -------------------------------------------------------------

via_systemd=0
via_open=0
running_unit="$UNIT"
[ "$legacy" = 1 ] && running_unit="strixrun.service"
old_cmd=enjrorun
[ "$legacy" = 1 ] && old_cmd=strixrun
if command -v systemctl >/dev/null && systemctl --user is-active --quiet "$running_unit" 2>/dev/null; then
  via_systemd=1
  say "Stopping the $running_unit user service"
  systemctl --user stop "$running_unit"
elif [ -n "$before" ]; then
  if "$old_cmd" stop 2>/dev/null | grep -q "Stopped"; then
    via_open=1
    say "Stopped the running server"
  fi
fi

restart() {
  [ "$RESTART" = 1 ] || { say "Not restarting (--no-restart)"; return 0; }
  if [ "$via_systemd" = 1 ]; then
    say "Starting the $UNIT user service"
    systemctl --user start "$UNIT"
  elif [ "$via_open" = 1 ]; then
    say "Starting Enjro Run in the background"
    enjrorun open --no-browser
  fi
}

# ---- install -----------------------------------------------------------------------------

say "Installing $TARGET"
# --reinstall-package rebuilds Enjro Run (and refetches the Git ref) even when the version
# number hasn't changed; dependencies come from uv's cache.
if ! uv tool install --force --reinstall-package enjrorun "$TARGET"; then
  if [ -n "$before" ]; then warn "the update failed; Enjro Run $before is still installed"; else warn "the install failed"; fi
  restart
  exit 1
fi

if ! command -v enjrorun >/dev/null; then
  warn "enjrorun isn't on your PATH; run 'uv tool update-shell' and open a new terminal"
  export PATH="$HOME/.local/bin:$PATH"
fi
after="$(enjrorun version 2>/dev/null || echo unknown)"

if [ "$legacy" = 1 ] && uv tool list 2>/dev/null | grep -q '^strixrun '; then
  say "Removing the old strixrun command"
  uv tool uninstall strixrun >/dev/null || warn "could not uninstall the strixrun tool; remove it with: uv tool uninstall strixrun"
fi
# Moves ~/.strix/run and a strixrun.service unit over; prints nothing when there's nothing to do.
migrated="$(enjrorun migrate 2>&1)" || die "moving StrixRun's data failed: $migrated"
[ "$migrated" = "Nothing to migrate." ] || say "$migrated"

restart

if [ -n "$before" ] && [ "$before" = "$after" ]; then
  say "Done: Enjro Run $after (same version number, rebuilt from ${REF:-main})"
else
  say "Done: Enjro Run ${before:+$before -> }$after"
fi
if [ "$via_systemd" = 0 ] && [ "$via_open" = 0 ] && [ "$RESTART" = 1 ]; then
  echo "    Enjro Run wasn't running. Start it with: enjrorun serve   (or: enjrorun open)"
fi
