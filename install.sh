#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🥁 Instalando o Batuque no seu usuário..."

# 1. Garantir que os artefatos estejam compilados
bash "${PROJECT_DIR}/packaging/build.sh"

# 2. Instalar executável em ~/.local/bin/batuque e aliases
mkdir -p "${HOME}/.local/bin"
install -m 0755 "${PROJECT_DIR}/dist/batuque" "${HOME}/.local/bin/batuque"
ln -sf "${HOME}/.local/bin/batuque" "${HOME}/.local/bin/batuque-gui"
# Manter alias soundbee para compatibilidade
ln -sf "${HOME}/.local/bin/batuque" "${HOME}/.local/bin/soundbee"
ln -sf "${HOME}/.local/bin/batuque" "${HOME}/.local/bin/soundbee-gui"

# 3. Instalar ícones vetoriais SVG no tema Hicolor
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
mkdir -p "${ICON_DIR}"
install -m 0644 "${PROJECT_DIR}/data/icons/org.batuque.Batuque.svg" "${ICON_DIR}/org.batuque.Batuque.svg"
install -m 0644 "${PROJECT_DIR}/data/icons/org.soundbee.SoundBee.svg" "${ICON_DIR}/org.soundbee.SoundBee.svg"

# 4. Instalar atalho .desktop no menu de aplicativos
APP_DIR="${HOME}/.local/share/applications"
mkdir -p "${APP_DIR}"
rm -f "${APP_DIR}/org.soundbee.SoundBee.desktop"
sed "s|^Exec=batuque|Exec=${HOME}/.local/bin/batuque|" \
    "${PROJECT_DIR}/data/org.batuque.Batuque.desktop" > "${APP_DIR}/org.batuque.Batuque.desktop"
chmod 0644 "${APP_DIR}/org.batuque.Batuque.desktop"

# 5. Instalar metadados AppStream
META_DIR="${HOME}/.local/share/metainfo"
mkdir -p "${META_DIR}"
install -m 0644 "${PROJECT_DIR}/data/org.batuque.Batuque.metainfo.xml" "${META_DIR}/org.batuque.Batuque.metainfo.xml"

# 6. Atualizar caches do desktop (KDE Plasma / GNOME)
update-desktop-database "${APP_DIR}" 2>/dev/null || true
gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true

echo ""
echo "✅ Batuque instalado com sucesso!"
echo "   • Executável : ~/.local/bin/batuque (e alias ~/.local/bin/soundbee)"
echo "   • Ícone      : ~/.local/share/icons/hicolor/scalable/apps/org.batuque.Batuque.svg"
echo "   • Atalho Menu: ~/.local/share/applications/org.batuque.Batuque.desktop"
