#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${PROJECT_DIR}/dist"
VERSION="0.1.0"
NAME="batuque"

echo "==> Preparando diretório dist/ em ${DIST_DIR}..."
rm -rf "${DIST_DIR}"
mkdir -p "${DIST_DIR}"

# ------------------------------------------------------------------------------
# 1. Gerar Executável Portátil Único (Python ZipApp: dist/batuque)
# ------------------------------------------------------------------------------
echo "==> [1/2] Gerando executável portátil autocontido (dist/batuque)..."
BUILD_STAGE="$(mktemp -d)"
trap 'rm -rf "${BUILD_STAGE}"' EXIT

mkdir -p "${BUILD_STAGE}/app"
cp -r "${PROJECT_DIR}/batuque" "${BUILD_STAGE}/app/"
cp -r "${PROJECT_DIR}/soundbee" "${BUILD_STAGE}/app/"
# Remove __pycache__
find "${BUILD_STAGE}/app" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

cat << 'EOF' > "${BUILD_STAGE}/app/__main__.py"
import sys
from batuque.__main__ import run_entrypoint
sys.exit(run_entrypoint() or 0)
EOF

python3 -m zipapp "${BUILD_STAGE}/app" \
    --output "${DIST_DIR}/batuque" \
    --python "/usr/bin/env python3" \
    --compress

chmod +x "${DIST_DIR}/batuque"
ln -sf "batuque" "${DIST_DIR}/soundbee"
echo "    -> Executável criado: ${DIST_DIR}/batuque (com link ${DIST_DIR}/soundbee)"

# ------------------------------------------------------------------------------
# 2. Gerar Pacote Instalador Nativo RPM (dist/batuque-0.1.0-1.*.noarch.rpm)
# ------------------------------------------------------------------------------
if command -v rpmbuild >/dev/null 2>&1; then
    echo "==> [2/2] Gerando instalador .rpm nativo para Fedora..."
    RPM_TOPDIR="${BUILD_STAGE}/rpmbuild"
    mkdir -p "${RPM_TOPDIR}"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

    TAR_DIR="${BUILD_STAGE}/${NAME}-${VERSION}"
    mkdir -p "${TAR_DIR}"
    cp -r "${PROJECT_DIR}/batuque" "${PROJECT_DIR}/soundbee" "${PROJECT_DIR}/data" "${PROJECT_DIR}/README.md" "${PROJECT_DIR}/pyproject.toml" "${TAR_DIR}/"
    find "${TAR_DIR}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

    tar -czf "${RPM_TOPDIR}/SOURCES/${NAME}-${VERSION}.tar.gz" -C "${BUILD_STAGE}" "${NAME}-${VERSION}"
    cp "${PROJECT_DIR}/packaging/batuque.spec" "${RPM_TOPDIR}/SPECS/"

    rpmbuild --define "_topdir ${RPM_TOPDIR}" -bb "${RPM_TOPDIR}/SPECS/batuque.spec"

    find "${RPM_TOPDIR}/RPMS" -name "*.rpm" -exec cp {} "${DIST_DIR}/" \;
    echo "    -> Pacote RPM criado em: $(ls "${DIST_DIR}"/*.rpm)"
else
    echo "==> [Aviso] rpmbuild não encontrado; pulando geração do .rpm."
fi

echo ""
echo "✅ Build concluído com sucesso! Arquivos gerados em ${DIST_DIR}:"
ls -lh "${DIST_DIR}"
