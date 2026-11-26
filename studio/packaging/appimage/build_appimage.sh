#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDIO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${STUDIO_DIR}/.." && pwd)"

BUILD_DIR="${STUDIO_DIR}/build/release"
TOOLS_DIR="${REPO_ROOT}/build-tools"
DIST_DIR="${REPO_ROOT}/dist"
WORK_DIR="$(mktemp -d)"
APPDIR="${WORK_DIR}/AppDir"

cleanup() {
    rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

LINUXDEPLOY="${TOOLS_DIR}/linuxdeploy-x86_64.AppImage"
LINUXDEPLOY_QT="${TOOLS_DIR}/linuxdeploy-plugin-qt-x86_64.AppImage"
APPIMAGETOOL="${TOOLS_DIR}/appimagetool-x86_64.AppImage"

for tool in "${LINUXDEPLOY}" "${LINUXDEPLOY_QT}" "${APPIMAGETOOL}"; do
    if [[ ! -x "${tool}" ]]; then
        echo "ERROR: Missing executable AppImage tool: ${tool}" >&2
        echo "Run tools/fetch_third_party.sh first." >&2
        exit 1
    fi
done

if [[ ! -x "${BUILD_DIR}/mcu-harness-studio" ]]; then
    echo "Building studio release preset first..."
    cmake --preset release -S "${STUDIO_DIR}"
    cmake --build --preset release
fi

mkdir -p "${DIST_DIR}" "${APPDIR}"
cmake --install "${BUILD_DIR}" --prefix "${APPDIR}/usr"

# Make sure linuxdeploy finds the Qt plugin right next to itself or on PATH
export PATH="${TOOLS_DIR}:${PATH}"
export APPIMAGE_EXTRACT_AND_RUN=1
export ARCH=x86_64
export NO_STRIP=1
export QMAKE="${QMAKE:-$(command -v qmake6 || echo /usr/lib/qt6/bin/qmake6)}"
export QML_SOURCES_PATHS="${STUDIO_DIR}/qml"
export EXTRA_QT_PLUGINS="svg"
export OUTPUT="${DIST_DIR}/MCU_Harness_Studio-1.0.0-x86_64.AppImage"

DESKTOP_FILE="${STUDIO_DIR}/packaging/linux/mcu-harness-studio.desktop"
ICON_FILE="${BUILD_DIR}/icons/mcu-harness-studio-256.png"
if [[ ! -f "${ICON_FILE}" ]]; then
    ICON_FILE="${STUDIO_DIR}/resources/app/mcu-harness-studio.svg"
fi

# Note: MCU Harness Studio invokes system python3 (>=3.10) with bundled
# AppDir/usr/share/mcu-harness-studio/python/mcu_harness on PYTHONPATH.
"${LINUXDEPLOY}" \
    --appimage-extract-and-run \
    --appdir "${APPDIR}" \
    --plugin qt \
    --desktop-file "${DESKTOP_FILE}" \
    --icon-file "${ICON_FILE}"

# Refresh desktop/icon in AppDir top-level cleanly for appimagetool
cp -f "${DESKTOP_FILE}" "${APPDIR}/mcu-harness-studio.desktop"
if [[ -f "${BUILD_DIR}/icons/mcu-harness-studio-256.png" ]]; then
    cp -f "${BUILD_DIR}/icons/mcu-harness-studio-256.png" "${APPDIR}/mcu-harness-studio.png"
fi

RUNTIME_FILE="${TOOLS_DIR}/runtime-x86_64"
APPIMAGETOOL_ARGS=("--appimage-extract-and-run")
if [[ -f "${RUNTIME_FILE}" ]]; then
    APPIMAGETOOL_ARGS+=("--runtime-file" "${RUNTIME_FILE}")
fi

"${APPIMAGETOOL}" \
    "${APPIMAGETOOL_ARGS[@]}" \
    "${APPDIR}" \
    "${OUTPUT}"

chmod +x "${OUTPUT}"
echo "Built AppImage: ${OUTPUT}"
