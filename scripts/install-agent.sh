#!/bin/bash
# Render the launchd plist for this machine and load it. launchd expands
# neither ~ nor env vars inside a plist, so the absolute paths have to be
# baked in here rather than committed -- which also keeps the repo free of
# anyone's home directory.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.atreus.agent"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents"

sed -e "s|__ROOT__|$ROOT|g" -e "s|__HOME__|$HOME|g" \
    "$ROOT/scripts/$LABEL.plist.template" > "$TARGET"

# Unload any previous copy so a re-run picks up the regenerated plist;
# launchd caches it at bootstrap and ignores later edits on disk.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "loaded $LABEL"
