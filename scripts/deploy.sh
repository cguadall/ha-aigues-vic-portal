#!/usr/bin/env bash
# Copy the integration to a Home Assistant host over SSH.
#
# The files are first synced with rsync into a temporary folder owned by the
# SSH user, then installed with sudo in an interactive session, so sudo can ask
# for a password. Only custom_components/aigues_vic/ is sent.
#
# Usage: scripts/deploy.sh [--dry-run] [--restart] [--yes]
#
# Environment overrides:
#   AIGUES_VIC_REMOTE     SSH destination      (default carles@192.168.100.111)
#   AIGUES_VIC_DEST       custom_components dir
#                         (default /root/home-assistant/config/custom_components)
#   AIGUES_VIC_CONTAINER  container to restart (default homeassistant)
#   AIGUES_VIC_OWNER      owner of the files   (default root:root)

set -euo pipefail

REMOTE=${AIGUES_VIC_REMOTE:-carles@192.168.100.111}
DEST=${AIGUES_VIC_DEST:-/root/home-assistant/config/custom_components}
CONTAINER=${AIGUES_VIC_CONTAINER:-homeassistant}
OWNER=${AIGUES_VIC_OWNER:-root:root}
DOMAIN=aigues_vic

DRY_RUN=0
RESTART=0
ASSUME_YES=0

usage() {
    sed -n '2,15s/^# \{0,1\}//p' "$0"
}

for arg in "$@"; do
    case $arg in
        -n | --dry-run) DRY_RUN=1 ;;
        -r | --restart) RESTART=1 ;;
        -y | --yes) ASSUME_YES=1 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SRC="$REPO/custom_components/$DOMAIN/"
[[ -f "$SRC/manifest.json" ]] || { echo "Not found: $SRC/manifest.json" >&2; exit 1; }
command -v rsync >/dev/null || { echo "rsync is not installed locally" >&2; exit 1; }

VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$SRC/manifest.json")
COMMIT=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)
if [[ -n $(git -C "$REPO" status --porcelain -- "custom_components/$DOMAIN" 2>/dev/null) ]]; then
    COMMIT="$COMMIT+local changes"
fi

TARGET="$DEST/$DOMAIN"
echo "Integration: $DOMAIN $VERSION ($COMMIT)"
echo "Destination: $REMOTE:$TARGET"
((DRY_RUN)) && echo "Mode: dry run (nothing is changed)"

if ((!DRY_RUN && !ASSUME_YES)); then
    read -r -p "Continue? [y/N] " answer
    [[ $answer =~ ^[yYsS]$ ]] || exit 1
fi

# Reuse one SSH connection, so a password is asked at most once.
CONTROL_DIR=$(mktemp -d)
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$CONTROL_DIR/%C" -o ControlPersist=120)
STAGE=""

cleanup() {
    if [[ -n $STAGE ]]; then
        ssh "${SSH_OPTS[@]}" "$REMOTE" "rm -rf -- $(printf %q "$STAGE")" || true
    fi
    ssh "${SSH_OPTS[@]}" -O exit "$REMOTE" 2>/dev/null || true
    rm -rf "$CONTROL_DIR"
}
trap cleanup EXIT

ssh "${SSH_OPTS[@]}" "$REMOTE" "command -v rsync >/dev/null" \
    || { echo "rsync is not installed on $REMOTE (sudo apt install rsync)" >&2; exit 1; }
STAGE=$(ssh "${SSH_OPTS[@]}" "$REMOTE" "mktemp -d")

rsync -rlptz --delete --exclude __pycache__ --exclude '*.pyc' \
    -e "ssh ${SSH_OPTS[*]}" "$SRC" "$REMOTE:$STAGE/$DOMAIN/"

q() { printf %q "$1"; }
RSYNC_FLAGS="-rlpt --delete --checksum --itemize-changes --chown=$(q "$OWNER")"
((DRY_RUN)) && RSYNC_FLAGS="$RSYNC_FLAGS --dry-run"

REMOTE_SCRIPT="set -e
sudo test -d $(q "$(dirname "$DEST")") || { echo 'Missing $(dirname "$DEST"): check AIGUES_VIC_DEST' >&2; exit 1; }
$( ((DRY_RUN)) || echo "sudo mkdir -p $(q "$TARGET")")
sudo rsync $RSYNC_FLAGS $(q "$STAGE/$DOMAIN/") $(q "$TARGET/")"
if ((RESTART && !DRY_RUN)); then
    REMOTE_SCRIPT="$REMOTE_SCRIPT
sudo docker restart $(q "$CONTAINER")"
fi

# -t gives sudo a terminal for the password prompt.
ssh "${SSH_OPTS[@]}" -t "$REMOTE" "$REMOTE_SCRIPT"

if ((DRY_RUN)); then
    echo "Dry run finished."
elif ((RESTART)); then
    echo "Installed and restarted $CONTAINER."
else
    echo "Installed. Restart Home Assistant to load the changes."
fi
