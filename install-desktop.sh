#!/usr/bin/env bash
# Installe un raccourci « Dreame UI » dans le menu des applications (utilisateur courant).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
cp "$ROOT/dreameui/resources/assets/hub-icon.svg" "$ICONS/io.github.dreameui.svg"
cat > "$APPS/io.github.dreameui.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Dreame UI
Comment=Contrôle de ton robot aspirateur Dreame
Exec=env PYTHONPATH=$ROOT python3 -m dreameui
Icon=io.github.dreameui
Categories=Utility;
StartupWMClass=io.github.dreameui
DESKTOP
update-desktop-database "$APPS" 2>/dev/null || true
echo "Raccourci installé : $APPS/io.github.dreameui.desktop"
