#!/usr/bin/env bash
# Install or update Enjro Run on Linux or macOS with one command. Run it again to update.
#
#   curl -fsSL https://raw.githubusercontent.com/keiththornton/enjro-run/main/installer/install.sh | bash
#   installer/install.sh                    # newest release (same thing, from a clone)
#   installer/install.sh --version v0.2.0   # a specific release (also rolls back)
#   installer/install.sh --service          # also start Enjro Run at login
#   installer/install.sh --uninstall        # remove it (models and settings are kept)
#
# It installs uv if it's missing, downloads the release's prebuilt package (dashboard
# included, so Node isn't needed), checks its SHA-256, stops a running Enjro Run, installs,
# and starts it again. A StrixRun install (Enjro Run's old name) is replaced and its data
# moved over. Models, settings and the API key in ~/.enjro are always kept.

set -euo pipefail

# Everything runs inside this block, so bash has read the whole script before running any
# of it: safe for `curl | bash`, where commands could otherwise read the rest as input.
{

REPO="${ENJRORUN_REPO:-keiththornton/enjro-run}"
API="${ENJRORUN_API:-https://api.github.com}"
SOURCE="${ENJRORUN_SOURCE:-git+https://github.com/$REPO}"
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

No sign-in is needed. If GitHub rate-limits you, set GH_TOKEN (or sign in with gh).
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
  uv tool uninstall "$installed" >/dev/null 2>&1 || warn "uv couldn't uninstall $installed"
  say "Removed Enjro Run${before:+ $before}. Your models and settings are still in ~/.enjro; delete it to remove them."
  exit 0
fi

# ---- get the package ---------------------------------------------------------------------

token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -z "$token" ] && command -v gh >/dev/null; then
  token="$(gh auth token 2>/dev/null || true)"
fi

api_get() {  # api_get URL OUTFILE [accept]
  if [ -n "$token" ]; then
    curl -fsSL -H "Authorization: Bearer $token" -H "Accept: ${3:-application/vnd.github+json}" -o "$2" "$1"
  else
    curl -fsSL -H "Accept: ${3:-application/vnd.github+json}" -o "$2" "$1"
  fi
}

# Prints "<asset api url> <file name> <sha256 or empty>" for the release's wheel.
pick_wheel() {
  uv run --no-project --quiet --python ">=3.12" python -I - "$1" <<'EOF'
import json, sys
release = json.load(open(sys.argv[1]))
wheels = [a for a in release.get("assets", []) if a["name"].startswith("enjrorun-") and a["name"].endswith(".whl")]
if not wheels:
    sys.exit(f"release {release.get('tag_name')} has no enjrorun wheel")
a = wheels[0]
digest = a.get("digest") or ""
print(a["url"], a["name"], digest.split(":", 1)[1] if digest.startswith("sha256:") else "")
EOF
}

sha256_of() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

target=""
if [ -n "$WHEEL" ]; then
  [ -f "$WHEEL" ] || die "no such file: $WHEEL"
  target="$WHEEL"
elif [ -n "$FROM_SOURCE" ]; then
  command -v git >/dev/null || die "git not found; install it with your package manager"
  command -v npm >/dev/null || die "--from-source builds the dashboard and needs Node 20+ (npm not found)"
  target="$SOURCE@$FROM_SOURCE"
else
  if [ -n "$VERSION" ]; then release_url="$API/repos/$REPO/releases/tags/$VERSION"; else release_url="$API/repos/$REPO/releases/latest"; fi
  say "Looking up ${VERSION:-the newest release} of $REPO"
  if ! api_get "$release_url" "$TMP/release.json"; then
    if [ -z "$token" ]; then
      die "couldn't read $REPO's releases from GitHub (no network, or rate-limited: set GH_TOKEN and run this again)"
    fi
    die "couldn't find ${VERSION:-a release} in $REPO"
  fi
  read -r asset_url wheel_name wheel_sha < <(pick_wheel "$TMP/release.json") || die "no installable package in that release"
  say "Downloading $wheel_name"
  api_get "$asset_url" "$TMP/$wheel_name" application/octet-stream || die "download failed"
  if [ -n "$wheel_sha" ]; then
    [ "$(sha256_of "$TMP/$wheel_name")" = "$wheel_sha" ] || die "checksum mismatch for $wheel_name; not installing it"
  else
    warn "GitHub gave no checksum for $wheel_name; installing it unchecked"
  fi
  target="$TMP/$wheel_name"
fi

# ---- install -----------------------------------------------------------------------------

if [ -n "$installed" ]; then
  say "Installed now: $([ "$installed" = strixrun ] && echo StrixRun || echo "Enjro Run") ${before:-(unknown version)}"
fi
port_of() { "$1" config show 2>/dev/null | sed -n 's/.*"port": \([0-9]*\).*/\1/p' | head -1; }
was_running=0
if [ -n "$installed" ] && curl -fs -o /dev/null "http://127.0.0.1:$(port_of "$installed")/v1/models" 2>/dev/null; then
  was_running=1
fi
stop_running

say "Installing Enjro Run"
# --reinstall-package rebuilds from Git even when the version number hasn't changed.
reinstall=""
[ -n "$FROM_SOURCE" ] && reinstall="--reinstall-package=enjrorun"
if ! uv tool install --quiet --force --python ">=3.12" $reinstall "$target"; then
  warn "the install failed${before:+; the previous version ($before) is still installed}"
  if [ "$was_running" = 1 ] || [ "$service_active" = 1 ]; then
    "$installed" open --no-browser >/dev/null 2>&1 || true
  fi
  exit 1
fi
command -v enjrorun >/dev/null || die "enjrorun was installed but isn't on PATH; run 'uv tool update-shell' and open a new terminal"

if [ "$installed" = strixrun ] && uv tool list 2>/dev/null | grep -q '^strixrun '; then
  say "Removing the old strixrun command"
  uv tool uninstall strixrun >/dev/null || warn "couldn't remove strixrun; run: uv tool uninstall strixrun"
fi

# Moves ~/.strix/run and a StrixRun login service over. "Nothing to migrate." otherwise.
migrated="$(enjrorun migrate 2>&1)" || die "moving StrixRun's data failed: $migrated"
[ "$migrated" = "Nothing to migrate." ] || say "$migrated"

after="$(enjrorun version 2>/dev/null || echo unknown)"

# ---- login service and start -------------------------------------------------------------

want_service="$had_service"
[ "$SERVICE" = on ] && want_service=1
[ "$SERVICE" = off ] && want_service=0

if [ "$want_service" = 1 ]; then
  # (Re)writes the unit or agent so it points at this install, and starts it.
  say "$(enjrorun service install)"
  if [ "$START" = 0 ]; then
    if [ "$OS" = Linux ]; then systemctl --user stop "$UNIT"; else launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true; fi
  elif [ "$OS" = Linux ]; then
    systemctl --user restart "$UNIT"
    if command -v loginctl >/dev/null && [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]; then
      echo "    To keep it running when you're logged out (e.g. on a server): sudo loginctl enable-linger $USER"
    fi
  else
    launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
  fi
elif [ "$had_service" = 1 ]; then
  say "$(enjrorun service uninstall)"
fi

url=""
if [ "$START" = 1 ]; then
  if [ "$want_service" = 1 ]; then
    url="http://127.0.0.1:$(port_of enjrorun)"
    for _ in $(seq 1 60); do curl -fs -o /dev/null "$url/v1/models" && break; sleep 0.5; done
  else
    enjrorun open --no-browser >/dev/null || die "Enjro Run didn't start; see ~/.enjro/run/logs/serve.log"
    url="http://127.0.0.1:$(port_of enjrorun)"
  fi
fi

echo
if [ -n "$before" ] && [ "$before" != "$after" ]; then
  say "Enjro Run $before -> $after"
else
  say "Enjro Run $after is installed"
fi
[ -n "$url" ] && echo "    Dashboard and API: $url   (OpenAI API at $url/v1)"
[ "$START" = 0 ] && echo "    Start it with: enjrorun open"
[ "$want_service" = 1 ] && echo "    Starts at login (enjrorun service uninstall to stop that)"
if ! PATH="$USER_PATH" command -v enjrorun >/dev/null; then
  uv tool update-shell >/dev/null 2>&1 || true
  echo "    Open a new terminal to use the enjrorun command (it's in $(dirname "$(command -v enjrorun)"))."
fi
echo "    Update later by running this installer again."
exit 0
}
