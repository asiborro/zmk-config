#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} != "" && ${1:-} != "--with-tunnel" ]]; then
    printf 'Usage: %s [--with-tunnel]\n' "$0" >&2
    exit 2
fi
kit_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(dirname -- "$kit_dir")
install -d "$HOME/.local/lib/keyboard-telemetry" "$HOME/.local/bin" "$HOME/.config/systemd/user"
install -m 644 "$repo_dir/scripts/battery-monitor.py" "$repo_dir/scripts/waybar-battery.py" \
  "$repo_dir/scripts/trackball_telemetry.py" "$repo_dir/scripts/trackball_usb.py" \
  "$repo_dir/scripts/trackball_usb_monitor.py" "$HOME/.local/lib/keyboard-telemetry/"
if [[ -d "$HOME/.config/waybar/scripts" ]]; then
    install -m 755 "$repo_dir/scripts/waybar-battery.py" "$HOME/.config/waybar/scripts/waybar-battery.py"
    install -m 644 "$kit_dir/waybar-battery.css" "$HOME/.config/waybar/peamk-battery.css"
    /usr/bin/python3 - <<'PY'
from datetime import datetime
from pathlib import Path
import re
import shutil
style = Path.home() / ".config/waybar/style.css"
if style.exists() and '"peamk-battery.css"' not in style.read_text():
    backup = style.with_name("style.css.before-peamk-" + datetime.now().strftime("%Y%m%d-%H%M%S%f"))
    shutil.copy2(style, backup)
    style.write_text('@import "peamk-battery.css";\n' + style.read_text())
config = Path.home() / ".config/waybar/config.jsonc"
if config.exists():
    original = config.read_text()
    updated = re.sub(r'("exec"\s*:\s*"[^"\n]*waybar-battery\.py)(")',
                     r'\1 --device darkblue --address D5:B0:5B:EC:16:5D\2', original)
    if updated != original:
        shutil.copy2(config, config.with_name("config.jsonc.before-darkblue-" +
                                             datetime.now().strftime("%Y%m%d-%H%M%S%f")))
        config.write_text(updated)
PY
fi
cat > "$HOME/.local/bin/darkblue-battery" <<'EOF'
#!/usr/bin/env sh
exec /usr/bin/python3 "$HOME/.local/lib/keyboard-telemetry/battery-monitor.py" "$@"
EOF
chmod 755 "$HOME/.local/bin/darkblue-battery"
# Retain the old CLI as an alias for existing scripts and the historical DB.
ln -sfn darkblue-battery "$HOME/.local/bin/peamk-battery"
install -m 644 "$kit_dir/darkblue-battery.service" "$kit_dir/roseblue-trackball.service" \
  "$kit_dir/keyboard-telemetry-tunnel.service" "$HOME/.config/systemd/user/"
old_tunnel_active=false
if systemctl --user is-enabled --quiet peamk-battery-tunnel.service 2>/dev/null; then
    old_tunnel_active=true
fi
for old_service in peamk-battery.service peamk-battery-tunnel.service; do
    if [[ -f "$HOME/.config/systemd/user/$old_service" ]]; then
        systemctl --user disable --now "$old_service"
    fi
done
systemctl --user daemon-reload
systemctl --user enable darkblue-battery.service roseblue-trackball.service
systemctl --user restart darkblue-battery.service roseblue-trackball.service
if [[ ${1:-} == "--with-tunnel" || $old_tunnel_active == true ]]; then
    systemctl --user enable keyboard-telemetry-tunnel.service
    systemctl --user restart keyboard-telemetry-tunnel.service
fi
systemctl --user --no-pager --full status darkblue-battery.service roseblue-trackball.service
