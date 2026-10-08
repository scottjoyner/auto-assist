#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
account_home="$(getent passwd "$(id -un)" | cut -d: -f6)"
lib="$account_home/.local/lib/fleet"
systemd="$account_home/.config/systemd/user"

install -d -m 700 "$lib"
install -m 700 "$repo/scripts/provider_presence_watch.py" "$lib/provider_presence_watch.py"
install -d -m 755 "$systemd"
install -m 644 "$repo/deploy/systemd/fleet-provider-presence.service" "$systemd/fleet-provider-presence.service"
install -m 644 "$repo/deploy/systemd/fleet-provider-presence.timer" "$systemd/fleet-provider-presence.timer"
systemctl --user daemon-reload
systemctl --user enable --now fleet-provider-presence.timer
systemctl --user start fleet-provider-presence.service
systemctl --user show fleet-provider-presence.service -p Result -p ExecMainStatus
systemctl --user list-timers --all --no-pager | grep fleet-provider-presence
