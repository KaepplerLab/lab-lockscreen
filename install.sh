#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
    echo 'Run this as the lab desktop user, without sudo.' >&2
    exit 1
fi
if ! gnome-shell --version | grep -q 'GNOME Shell 46\.'; then
    echo 'This package targets Ubuntu 24.04 with GNOME Shell 46.' >&2
    exit 1
fi
/usr/bin/python3 -c 'import PIL, tomllib' || {
    echo 'Install dependencies: sudo apt install python3-pil fonts-dejavu-core' >&2
    exit 1
}
# Stop an in-flight run before replacing its code or configuration.
systemctl --user stop lab-lockscreen.timer lab-lockscreen.service 2>/dev/null || true
package_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$HOME/.local/share/lab-lockscreen"
config_dir="$HOME/.config/lab-lockscreen"
unit_dir="$HOME/.config/systemd/user"
extension_dir="$HOME/.local/share/gnome-shell/extensions/lab-lockscreen@local"
mkdir -p "$app_dir" "$config_dir" "$unit_dir" "$extension_dir"
cp "$package_dir/update.py" "$app_dir/update.py"
cp "$package_dir/extension/metadata.json" "$package_dir/extension/extension.js" "$extension_dir/"
if [[ ! -f "$config_dir/config.toml" ]]; then
    cp "$package_dir/config.toml" "$config_dir/config.toml"
fi
/usr/bin/python3 "$package_dir/migrate_config.py" "$config_dir/config.toml"
if [[ ! -f "$app_dir/original-picture-uri.txt" ]]; then
    gsettings get org.gnome.desktop.screensaver picture-uri > "$app_dir/original-picture-uri.txt"
fi
interval="$(/usr/bin/python3 - "$app_dir" "$config_dir/config.toml" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from update import load_config
print(load_config(sys.argv[2])["update"]["interval_seconds"])
PY
)"
# %h is systemd's home specifier. Quoting supports spaces in the home directory.
cat > "$unit_dir/lab-lockscreen.service" <<'UNIT'
[Unit]
Description=Refresh local lab space-weather lockscreen

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 "%h/.local/share/lab-lockscreen/update.py"
TimeoutStartSec=240
Nice=10
UMask=0077
Environment=PYTHONUNBUFFERED=1
UNIT
cat > "$unit_dir/lab-lockscreen.timer" <<UNIT
[Unit]
Description=Refresh lab lockscreen periodically

[Timer]
OnStartupSec=20s
OnUnitInactiveSec=${interval}s
AccuracySec=10s
Unit=lab-lockscreen.service

[Install]
WantedBy=timers.target
UNIT
# Create a valid first screen even when installation happens offline.
/usr/bin/python3 "$app_dir/update.py" --offline
systemctl --user daemon-reload
systemctl --user enable --now lab-lockscreen.timer
systemctl --user start --no-block lab-lockscreen.service
echo 'Installed. Log out and log in once, then run:'
echo '  gnome-extensions enable lab-lockscreen@local'
echo 'Configuration: ~/.config/lab-lockscreen/config.toml'
