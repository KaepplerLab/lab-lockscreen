#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -eq 0 ]]; then
    echo 'Run as the same desktop user who installed this package, without sudo.' >&2
    exit 1
fi
systemctl --user disable --now lab-lockscreen.timer 2>/dev/null || true
systemctl --user stop lab-lockscreen.service 2>/dev/null || true
gnome-extensions disable lab-lockscreen@local 2>/dev/null || true
original="$HOME/.local/share/lab-lockscreen/original-picture-uri.txt"
if [[ -f "$original" ]]; then
    gsettings set org.gnome.desktop.screensaver picture-uri "$(cat "$original")"
fi
rm -f "$HOME/.config/systemd/user/lab-lockscreen.service" "$HOME/.config/systemd/user/lab-lockscreen.timer"
rm -f "$HOME/.local/share/gnome-shell/extensions/lab-lockscreen@local/extension.js" "$HOME/.local/share/gnome-shell/extensions/lab-lockscreen@local/metadata.json"
systemctl --user daemon-reload
echo 'Disabled and restored the previous lockscreen setting. Configuration and cache retained.'
echo 'Log out and back in to finish unloading the extension.'
