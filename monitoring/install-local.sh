#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} != "" && ${1:-} != "--with-tunnel" ]]; then
    printf 'Usage: %s [--with-tunnel]\n' "$0" >&2
    exit 2
fi
kit_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(dirname -- "$kit_dir")
install -d "$HOME/.local/lib/peamk-battery" "$HOME/.local/bin" "$HOME/.config/systemd/user"
install -m 644 "$repo_dir/scripts/battery-monitor.py" "$repo_dir/scripts/waybar-battery.py" "$repo_dir/scripts/trackball_telemetry.py" "$HOME/.local/lib/peamk-battery/"
if [[ -d "$HOME/.config/waybar/scripts" ]]; then
    install -m 755 "$repo_dir/scripts/waybar-battery.py" "$HOME/.config/waybar/scripts/waybar-battery.py"
    install -m 644 "$kit_dir/waybar-battery.css" "$HOME/.config/waybar/peamk-battery.css"
    /usr/bin/python3 - <<'PY'
from datetime import datetime
from pathlib import Path
import shutil
style = Path.home() / ".config/waybar/style.css"
if style.exists() and '"peamk-battery.css"' not in style.read_text():
    backup = style.with_name("style.css.before-peamk-" + datetime.now().strftime("%Y%m%d-%H%M%S%f"))
    shutil.copy2(style, backup)
    style.write_text('@import "peamk-battery.css";\n' + style.read_text())
PY
fi
cat > "$HOME/.local/bin/peamk-battery" <<'EOF'
#!/usr/bin/env sh
exec /usr/bin/python3 "$HOME/.local/lib/peamk-battery/battery-monitor.py" "$@"
EOF
chmod 755 "$HOME/.local/bin/peamk-battery"
install -m 644 "$kit_dir/peamk-battery.service" "$kit_dir/peamk-battery-tunnel.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable peamk-battery.service
systemctl --user restart peamk-battery.service
if [[ ${1:-} == "--with-tunnel" ]]; then
    systemctl --user enable peamk-battery-tunnel.service
    systemctl --user restart peamk-battery-tunnel.service
fi
systemctl --user --no-pager --full status peamk-battery.service
