#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MANIFEST="${SCRIPT_DIR}/org.batuque.Batuque.yaml"
BUILD_DIR="${REPO_ROOT}/build/flatpak"
REPO_DIR="${REPO_ROOT}/build/flatpak-repo"

echo "🥁 Batuque - Construtor de Flatpak"
echo "=================================="

if ! command -v flatpak-builder &>/dev/null; then
    echo "❌ flatpak-builder não encontrado!"
    echo "Para instalar no Fedora:"
    echo "  sudo dnf install flatpak-builder"
    echo "Para instalar no Ubuntu/Debian:"
    echo "  sudo apt install flatpak-builder"
    echo "Para instalar no Arch Linux:"
    echo "  sudo pacman -S flatpak-builder"
    exit 1
fi

echo "📦 Verificando Runtimes GNOME 47..."
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true
flatpak install -y --noninteractive flathub org.gnome.Platform//47 org.gnome.Sdk//47 || true

echo "🔨 Construindo Flatpak localmente..."
mkdir -p "${BUILD_DIR}" "${REPO_DIR}"

flatpak-builder \
    --force-clean \
    --user \
    --install-deps-from=flathub \
    --repo="${REPO_DIR}" \
    "${BUILD_DIR}" \
    "${MANIFEST}"

echo ""
echo "✅ Build do Flatpak concluído com sucesso!"
echo "Para instalar localmente:"
echo "  flatpak --user remote-add --no-gpg-verify batuque-local ${REPO_DIR}"
echo "  flatpak --user install batuque-local org.batuque.Batuque"
echo "Para rodar:"
echo "  flatpak run org.batuque.Batuque"
