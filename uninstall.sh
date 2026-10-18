#!/usr/bin/env bash
set -euo pipefail

echo "🗑️ Removendo a instalação local do SoundBee..."

rm -f "${HOME}/.local/bin/soundbee"
rm -f "${HOME}/.local/bin/soundbee-gui"
rm -f "${HOME}/.local/share/icons/hicolor/scalable/apps/org.soundbee.SoundBee.svg"
rm -f "${HOME}/.local/share/applications/org.soundbee.SoundBee.desktop"
rm -f "${HOME}/.local/share/metainfo/org.soundbee.SoundBee.metainfo.xml"

update-desktop-database "${HOME}/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true

echo "✅ SoundBee removido do usuário local (seus dados e biblioteca em ~/.local/share/soundbee foram preservados)."
