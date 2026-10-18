#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🐝 Instalando o SoundBee no seu usuário..."

# 1. Garantir que os artefatos (executável + rpm) estejam compilados
bash "${PROJECT_DIR}/packaging/build.sh"

# 2. Instalar executável em ~/.local/bin/soundbee
mkdir -p "${HOME}/.local/bin"
install -m 0755 "${PROJECT_DIR}/dist/soundbee" "${HOME}/.local/bin/soundbee"
ln -sf "${HOME}/.local/bin/soundbee" "${HOME}/.local/bin/soundbee-gui"

# 3. Instalar ícone vetorial SVG no tema Hicolor
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
mkdir -p "${ICON_DIR}"
install -m 0644 "${PROJECT_DIR}/data/icons/org.soundbee.SoundBee.svg" "${ICON_DIR}/org.soundbee.SoundBee.svg"

# 4. Instalar atalho .desktop no menu de aplicativos
APP_DIR="${HOME}/.local/share/applications"
mkdir -p "${APP_DIR}"
sed "s|^Exec=soundbee|Exec=${HOME}/.local/bin/soundbee|" \
    "${PROJECT_DIR}/data/org.soundbee.SoundBee.desktop" > "${APP_DIR}/org.soundbee.SoundBee.desktop"
chmod 0644 "${APP_DIR}/org.soundbee.SoundBee.desktop"

# 5. Instalar metadados AppStream
META_DIR="${HOME}/.local/share/metainfo"
mkdir -p "${META_DIR}"
install -m 0644 "${PROJECT_DIR}/data/org.soundbee.SoundBee.metainfo.xml" "${META_DIR}/org.soundbee.SoundBee.metainfo.xml"

# 6. Atualizar caches do desktop (KDE Plasma / GNOME)
update-desktop-database "${APP_DIR}" 2>/dev/null || true
gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true

echo ""
echo "✅ SoundBee instalado com sucesso!"
echo "   • Executável : ~/.local/bin/soundbee"
echo "   • Ícone      : ~/.local/share/icons/hicolor/scalable/apps/org.soundbee.SoundBee.svg"
echo "   • Atalho Menu: ~/.local/share/applications/org.soundbee.SoundBee.desktop"
echo "   • Pacote RPM : $(ls "${PROJECT_DIR}"/dist/*.rpm 2>/dev/null || echo 'N/A')"
