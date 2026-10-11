#!/usr/bin/env bash
# Install or update Enjro Run on Linux or macOS with one command. Run it again to update.
#
#   installer/install.sh                    # newest release
#   installer/install.sh --version v0.2.0   # a specific release (also rolls back)
#   installer/install.sh --service          # also start Enjro Run at login
#   installer/install.sh --uninstall        # remove it (models and settings are kept)
#
# It installs uv if it's missing, downloads the release's prebuilt package (dashboard
# included, so Node isn't needed), checks its SHA-256, stops a running Enjro Run, installs,
# and starts it again. A StrixRun install (Enjro Run's old name) is replaced and its data
# moved over. Models, settings and the API key in ~/.enjro are always kept.

set -euo pipefail

REPO="${ENJRORUN_REPO:-keiththornton/strixrun}"
API="${ENJRORUN_API:-https://api.github.com}"
SOURCE="${ENJRORUN_SOURCE:-git+ssh://git@github.com/$REPO}"
VERSION=""
WHEEL=""
FROM_SOURCE=""
SERVICE=""
START=1
UNINSTALL=0
UNIT="enjrorun.service"
LABEL="com.enjrorun.serve"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  --version TAG       install this release, e.g. v0.2.0 (default: the newest)
  --service           start Enjro Run at login (systemd user unit / launchd agent)
  --no-service        remove the login service if there is one
  --no-start          leave Enjro Run stopped afterwards
  --uninstall         remove Enjro Run; models and settings in ~/.enjro are kept
  --wheel FILE        install this enjrorun-*.whl instead of downloading one
  --from-source [REF] build from Git instead (needs git and Node 20+), at REF
                      (default: main), from \$ENJRORUN_SOURCE or $SOURCE
  -h, --help          show this help

The repository is private: downloads use \`gh\` if you're signed in (gh auth login), or a
token in GH_TOKEN / GITHUB_TOKEN. Once it's public, no sign-in is needed.
EOF
}

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || die "--version needs a value"; VERSION="$2"; shift 2 ;;
    --version=*) VERSION="${1#*=}"; shift ;;
    --wheel) [ $# -ge 2 ] || die "--wheel needs a file"; WHEEL="$2"; shift 2 ;;
    --wheel=*) WHEEL="${1#*=}"; shift ;;
    --from-source)
      FROM_SOURCE="main"
      if [ $# -ge 2 ] && [ "${2#-}" = "$2" ]; then FROM_SOURCE="$2"; shift; fi
      shift ;;
    --from-source=*) FROM_SOURCE="${1#*=}"; shift ;;
    --service) SERVICE=on; shift ;;
    --no-service) SERVICE=off; shift ;;
    --no-start) START=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done
[ -n "$WHEEL" ] && [ -n "$FROM_SOURCE" ] && die "use --wheel or --from-source, not both"
[ -n "$VERSION" ] && { [ -n "$WHEEL" ] || [ -n "$FROM_SOURCE" ]; } && die "--version only applies to release downloads"
case "$VERSION" in ""|v*) ;; *) VERSION="v$VERSION" ;; esac

OS="$(uname -s)"
case "$OS" in
  Linux|Darwin) ;;
  *) die "this installer is for Linux and macOS; on Windows run EnjroRun-Setup-<version>.exe" ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---- uv ----------------------------------------------------------------------------------

USER_PATH="$PATH"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  [ "$UNINSTALL" = 1 ] && die "uv isn't installed, so Enjro Run isn't either"
  say "Installing uv (Python package manager) into ~/.local/bin"
  command -v curl >/dev/null || die "curl not found; install it with your package manager"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
  command -v uv >/dev/null || die "uv didn't install; see https://docs.astral.sh/uv/getting-started/installation/"
fi

# ---- what's installed and running --------------------------------------------------------

installed=""   # enjrorun | strixrun | empty
before=""
if command -v enjrorun >/dev/null; then
  installed=enjrorun
elif command -v strixrun >/dev/null; then
  installed=strixrun
fi
[ -n "$installed" ] && before="$("$installed" version 2>/dev/null || true)"

had_service=0
service_active=0
if [ "$OS" = Linux ]; then
  for unit in enjrorun.service strixrun.service; do
    if [ -f "$HOME/.config/systemd/user/$unit" ]; then
      had_service=1
      systemctl --user is-active --quiet "$unit" 2>/dev/null && service_active=1
    fi
  done
else
  for label in com.enjrorun.serve com.strixrun.serve; do
    if [ -f "$HOME/Library/LaunchAgents/$label.plist" ]; then
      had_service=1
      launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 && service_active=1
    fi
  done
fi

stop_running() {
  if [ "$OS" = Linux ]; then
    for unit in enjrorun.service strixrun.service; do
      if systemctl --user is-active --quiet "$unit" 2>/dev/null; then
        say "Stopping the $unit user service"
        systemctl --user stop "$unit"
      fi
    done
  else
    for label in com.enjrorun.serve com.strixrun.serve; do
      if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
        say "Stopping the $label launchd agent"
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      fi
    done
  fi
  if [ -n "$installed" ] && "$installed" stop 2>/dev/null | grep -q "Stopped"; then
    say "Stopped the running server"
  fi
}

# ---- uninstall ---------------------------------------------------------------------------

if [ "$UNINSTALL" = 1 ]; then
  [ -n "$installed" ] || die "Enjro Run isn't installed"
  stop_running
  "$installed" service uninstall >/dev/null 2>&1 || true
