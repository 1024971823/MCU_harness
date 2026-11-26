#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FONTS_DIR="${REPO_ROOT}/studio/resources/fonts"
ICONS_DIR="${REPO_ROOT}/studio/resources/icons"
TWEETNACL_DIR="${REPO_ROOT}/studio/third_party/tweetnacl"
BUILD_TOOLS_DIR="${REPO_ROOT}/build-tools"

mkdir -p "${FONTS_DIR}" "${ICONS_DIR}" "${TWEETNACL_DIR}" "${BUILD_TOOLS_DIR}"

TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

curl_fetch() {
    local url="$1"
    local dest="$2"
    curl -fL --retry 3 -H "User-Agent: mcu-harness-studio-builder" -o "${dest}" "${url}"
}

github_first_zip_url() {
    local api_url="$1"
    local json_file="${TMP_DIR}/release.json"
    curl_fetch "${api_url}" "${json_file}"
    python3 - "${json_file}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
for asset in data.get("assets", []):
    url = asset.get("browser_download_url", "")
    if url.lower().endswith(".zip"):
        print(url)
        sys.exit(0)
sys.exit(1)
PY
}

echo "=== 1. Inter font (OFL) ==="
if [[ ! -f "${FONTS_DIR}/Inter-Regular.ttf" || ! -f "${FONTS_DIR}/Inter-Medium.ttf" || ! -f "${FONTS_DIR}/Inter-SemiBold.ttf" || ! -f "${FONTS_DIR}/Inter-Bold.ttf" || ! -f "${FONTS_DIR}/Inter-OFL.txt" ]]; then
    INTER_URL="$(github_first_zip_url "https://api.github.com/repos/rsms/inter/releases/latest")"
    INTER_ZIP="${TMP_DIR}/inter.zip"
    INTER_EX="${TMP_DIR}/inter"
    curl_fetch "${INTER_URL}" "${INTER_ZIP}"
    mkdir -p "${INTER_EX}"
    python3 -m zipfile -e "${INTER_ZIP}" "${INTER_EX}"
    for fname in Inter-Regular.ttf Inter-Medium.ttf Inter-SemiBold.ttf Inter-Bold.ttf; do
        found="$(find "${INTER_EX}" -type f -name "${fname}" | grep -v "Display" | head -n 1 || true)"
        if [[ -z "${found}" ]]; then
            found="$(find "${INTER_EX}" -type f -name "${fname}" | head -n 1 || true)"
        fi
        if [[ -z "${found}" ]]; then
            echo "ERROR: Missing font file ${fname} in Inter archive" >&2
            exit 1
        fi
        cp -f "${found}" "${FONTS_DIR}/${fname}"
    done
    inter_lic="$(find "${INTER_EX}" -type f \( -iname "LICENSE.txt" -o -iname "OFL.txt" -o -iname "*LICENSE*" \) | head -n 1 || true)"
    if [[ -n "${inter_lic}" ]]; then
        cp -f "${inter_lic}" "${FONTS_DIR}/Inter-OFL.txt"
    else
        curl_fetch "https://raw.githubusercontent.com/rsms/inter/master/LICENSE.txt" "${FONTS_DIR}/Inter-OFL.txt"
    fi
else
    echo "Inter fonts already present."
fi

echo "=== 2. JetBrains Mono font (OFL) ==="
if [[ ! -f "${FONTS_DIR}/JetBrainsMono-Regular.ttf" || ! -f "${FONTS_DIR}/JetBrainsMono-Medium.ttf" || ! -f "${FONTS_DIR}/JetBrainsMono-Bold.ttf" || ! -f "${FONTS_DIR}/JetBrainsMono-OFL.txt" ]]; then
    JB_URL="$(github_first_zip_url "https://api.github.com/repos/JetBrains/JetBrainsMono/releases/latest")"
    JB_ZIP="${TMP_DIR}/jbm.zip"
    JB_EX="${TMP_DIR}/jbm"
    curl_fetch "${JB_URL}" "${JB_ZIP}"
    mkdir -p "${JB_EX}"
    python3 -m zipfile -e "${JB_ZIP}" "${JB_EX}"
    for fname in JetBrainsMono-Regular.ttf JetBrainsMono-Medium.ttf JetBrainsMono-Bold.ttf; do
        found="$(find "${JB_EX}" -type f -name "${fname}" | grep -E '/ttf/' | head -n 1 || true)"
        if [[ -z "${found}" ]]; then
            found="$(find "${JB_EX}" -type f -name "${fname}" | head -n 1 || true)"
        fi
        if [[ -z "${found}" ]]; then
            echo "ERROR: Missing font file ${fname} in JetBrains Mono archive" >&2
            exit 1
        fi
        cp -f "${found}" "${FONTS_DIR}/${fname}"
    done
    jb_lic="$(find "${JB_EX}" -type f \( -iname "OFL.txt" -o -iname "LICENSE*" \) | head -n 1 || true)"
    if [[ -n "${jb_lic}" ]]; then
        cp -f "${jb_lic}" "${FONTS_DIR}/JetBrainsMono-OFL.txt"
    else
        curl_fetch "https://raw.githubusercontent.com/JetBrains/JetBrainsMono/master/OFL.txt" "${FONTS_DIR}/JetBrainsMono-OFL.txt"
    fi
else
    echo "JetBrains Mono fonts already present."
fi

echo "=== 3. Lucide icons (ISC) ==="
REQUESTED_ICONS=(
    bot sparkles cpu microchip circuit-board terminal square-terminal scroll-text
    activity gauge layers list-tree history shield shield-check shield-alert shield-off
    settings settings-2 key-round badge-check download refresh-cw rotate-ccw play pause
    square circle-stop power plug plug-zap unplug send arrow-up plus x check
    circle-check circle-x circle-alert triangle-alert info chevron-right chevron-down
    chevron-up chevron-left search command copy file-code-2 file-diff folder-open folder
    trash-2 filter eye eye-off wrench hammer flask-conical bug zap wand-sparkles
    message-square message-square-plus panel-left panel-bottom panel-right languages
    sun moon monitor bell external-link upload file-down clipboard-copy user lock unlock
    hard-drive waypoints timer binary variable list-checks circle-dot loader-circle
    ellipsis keyboard pin
)

need_lucide=0
for ic in "${REQUESTED_ICONS[@]}"; do
    if [[ ! -f "${ICONS_DIR}/${ic}.svg" ]]; then
        need_lucide=1
        break
    fi
done

if [[ "${need_lucide}" -eq 1 || ! -f "${ICONS_DIR}/LUCIDE-LICENSE.txt" ]]; then
    LUCIDE_TGZ="${TMP_DIR}/lucide.tar.gz"
    LUCIDE_EX="${TMP_DIR}/lucide"
    curl_fetch "https://github.com/lucide-icons/lucide/archive/refs/heads/main.tar.gz" "${LUCIDE_TGZ}"
    mkdir -p "${LUCIDE_EX}"
    tar -xzf "${LUCIDE_TGZ}" -C "${LUCIDE_EX}"
    LUCIDE_ROOT="$(find "${LUCIDE_EX}" -maxdepth 1 -mindepth 1 -type d | head -n 1)"
    LUCIDE_ICONS_SRC="${LUCIDE_ROOT}/icons"

    # Substitution map when Lucide main renames/removes icons (recorded in script):
    # - history -> clock-rewind / rotate-ccw / clock
    # - shield-alert -> shield-x / shield-question / shield
    # - circle-alert -> octagon-alert / badge-alert / info
    # - triangle-alert -> siren / octagon-alert / badge-alert / info
    # - file-code-2 -> file-code / file-braces / file-text / file
    # - trash-2 -> trash / eraser / x
    # - filter -> funnel / list-filter / sliders-horizontal
    # - unlock -> lock-open / key-round
    resolve_fallback() {
        case "$1" in
            history) echo "clock-rewind rotate-ccw clock timer" ;;
            shield-alert) echo "shield-x shield-question-mark shield-off shield" ;;
            circle-alert) echo "octagon-alert badge-alert info" ;;
            triangle-alert) echo "octagon-alert badge-alert siren info" ;;
            file-code-2) echo "file-code file-braces file-text file" ;;
            trash-2) echo "trash eraser delete x" ;;
            filter) echo "funnel list-filter sliders-horizontal" ;;
            unlock) echo "lock-open key-round lock" ;;
            circle-stop) echo "octagon-x square-x square" ;;
            square-terminal) echo "terminal-square terminal" ;;
            circle-check) echo "check-circle check" ;;
            circle-x) echo "x-circle x" ;;
            *) echo "" ;;
        esac
    }

    missing=()
    for ic in "${REQUESTED_ICONS[@]}"; do
        src="${LUCIDE_ICONS_SRC}/${ic}.svg"
        if [[ -f "${src}" ]]; then
            cp -f "${src}" "${ICONS_DIR}/${ic}.svg"
        else
            echo "MISSING in upstream Lucide: ${ic}"
            replaced=""
            for cand in $(resolve_fallback "${ic}"); do
                if [[ -f "${LUCIDE_ICONS_SRC}/${cand}.svg" ]]; then
                    cp -f "${LUCIDE_ICONS_SRC}/${cand}.svg" "${ICONS_DIR}/${ic}.svg"
                    echo "  -> Substituted ${ic}.svg with nearest Lucide icon: ${cand}.svg"
                    replaced="${cand}"
                    break
                fi
            done
            if [[ -z "${replaced}" ]]; then
                echo "  Nearby matches in icons/: $(cd "${LUCIDE_ICONS_SRC}" && ls *${ic%%-*}* 2>/dev/null | head -n 10 | tr '\n' ' ')"
                missing+=("${ic}")
            fi
        fi
    done
    if [[ "${#missing[@]}" -gt 0 ]]; then
        echo "ERROR: Unresolved Lucide icons: ${missing[*]}" >&2
        exit 1
    fi
    cp -f "${LUCIDE_ROOT}/LICENSE" "${ICONS_DIR}/LUCIDE-LICENSE.txt"
else
    echo "Lucide icons already present."
fi

echo "=== 4. TweetNaCl (Public Domain) ==="
if [[ ! -f "${TWEETNACL_DIR}/tweetnacl.c" || ! -f "${TWEETNACL_DIR}/tweetnacl.h" ]]; then
    curl_fetch "https://tweetnacl.cr.yp.to/20140427/tweetnacl.c" "${TWEETNACL_DIR}/tweetnacl.c"
    curl_fetch "https://tweetnacl.cr.yp.to/20140427/tweetnacl.h" "${TWEETNACL_DIR}/tweetnacl.h"
else
    echo "TweetNaCl already present."
fi

echo "=== 5. AppImage tools ==="
download_tool() {
    local url="$1"
    local dest="$2"
    if [[ ! -x "${dest}" ]]; then
        curl_fetch "${url}" "${dest}"
        chmod +x "${dest}"
    fi
}
download_tool "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage" "${BUILD_TOOLS_DIR}/linuxdeploy-x86_64.AppImage"
download_tool "https://github.com/linuxdeploy/linuxdeploy-plugin-qt/releases/download/continuous/linuxdeploy-plugin-qt-x86_64.AppImage" "${BUILD_TOOLS_DIR}/linuxdeploy-plugin-qt-x86_64.AppImage"
download_tool "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" "${BUILD_TOOLS_DIR}/appimagetool-x86_64.AppImage"
if [[ ! -f "${BUILD_TOOLS_DIR}/runtime-x86_64" ]]; then
    curl_fetch "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64" "${BUILD_TOOLS_DIR}/runtime-x86_64"
fi

echo "All third-party assets fetched cleanly."
