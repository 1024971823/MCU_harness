# Packaging, Distribution, Licensing & Signed Updates

## 1. Prerequisites

Fetch third-party fonts (`Inter`, `JetBrains Mono`), `Lucide` icons, `TweetNaCl`, and Linux AppImage packaging binaries (`linuxdeploy`, `linuxdeploy-plugin-qt`, `appimagetool`):

```bash
bash tools/fetch_third_party.sh
```

---

## 2. Linux Packaging (`.deb` and `.AppImage`)

### Build Release & Run QtTests

```bash
cd studio
cmake --preset release
cmake --build --preset release
ctest --preset release --output-on-failure
```

### Build `.deb` Package (CPack)

```bash
cd studio
cmake --build --preset release --target package
# Output: ../dist/mcu-harness-studio_1.0.0_amd64.deb
dpkg-deb -I ../dist/mcu-harness-studio_1.0.0_amd64.deb
```

### Build `.AppImage`

```bash
bash studio/packaging/appimage/build_appimage.sh
# Output: dist/MCU_Harness_Studio-1.0.0-x86_64.AppImage
```

> **Note**: The AppImage bundles the Qt 6 QML GUI (`mcu-harness-studio`) and embedded Python engine sources (`usr/share/mcu-harness-studio/python/mcu_harness`), and invokes the host `python3` (`>= 3.10`) runtime to execute the backend RPC engine.

---

## 3. Windows Packaging (Inno Setup 6)

On Windows (MSVC 2022 + Qt 6.8+):

1. Build `studio/build/release/mcu-harness-studio.exe` (`cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -B studio/build/release -S studio && cmake --build studio/build/release`).
2. Stage files into `studio/build/windows-stage/`:
   - `mcu-harness-studio.exe` + `windeployqt --release --qmldir studio/qml`
   - `engine/mcu_harness/` (copied from `src/mcu_harness`)
   - `python/` (Python 3.12 embeddable distribution)
   - `icons/mcu-harness-studio.ico`
3. Compile `studio/packaging/windows/installer.iss` with Inno Setup 6 (`iscc studio/packaging/windows/installer.iss`) to generate `dist/MCU_Harness_Studio-1.0.0-x86_64-setup.exe`.

Automated cross-platform builds for both Linux and Windows are defined in `.github/workflows/studio-release.yml`.

---

## 4. Production Key Generation, License Signing & Updates

Never store production Ed25519 private keys inside the Git repository. Keep them offline under `~/.config/mcu-harness-vendor/` (`0600` permissions):

```bash
# 1. Generate vendor signing keys (outside repository)
python3 tools/license/mcu_license.py keygen --out ~/.config/mcu-harness-vendor/license_ed25519.pem
python3 tools/license/mcu_license.py pubkey --key ~/.config/mcu-harness-vendor/license_ed25519.pem --out studio/keys/license_pubkey.b64

python3 tools/license/mcu_license.py keygen --out ~/.config/mcu-harness-vendor/update_ed25519.pem
python3 tools/license/mcu_license.py pubkey --key ~/.config/mcu-harness-vendor/update_ed25519.pem --out studio/keys/update_pubkey.b64

# 2. Rebuild studio so the production public keys are embedded at compile time
cd studio && cmake --preset release && cmake --build --preset release

# 3. Issue a signed offline license (.lic)
python3 tools/license/mcu_license.py issue \
  --key ~/.config/mcu-harness-vendor/license_ed25519.pem \
  --licensee "Acme Robotics" --email "eng@acme.dev" \
  --edition pro --days 365 --features agent,hil,updates \
  --out dist/acme-pro.lic

# 4. Generate a signed update manifest (manifest.json + manifest.json.sig)
python3 tools/release/make_update_manifest.py \
  --key ~/.config/mcu-harness-vendor/update_ed25519.pem \
  --version 1.0.1 --channel stable \
  --notes-en docs/release_en.md --notes-zh docs/release_zh.md \
  --asset linux-x86_64-appimage=dist/MCU_Harness_Studio-1.0.1-x86_64.AppImage=https://cdn.mcuharness.dev/1.0.1/MCU_Harness_Studio-1.0.1-x86_64.AppImage \
  --asset linux-x86_64-deb=dist/mcu-harness-studio_1.0.1_amd64.deb=https://cdn.mcuharness.dev/1.0.1/mcu-harness-studio_1.0.1_amd64.deb \
  --out dist/manifest.json
```
