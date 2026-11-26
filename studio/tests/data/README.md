# Studio Test Keypair & Vectors (TEST-ONLY)

> **WARNING**: `test_ed25519_private.pem` in this directory is **strictly test-only** and MUST NEVER be used for production licenses or release manifests.

## Files

- `test_ed25519_private.pem` / `test_pubkey.b64`: Test-only Ed25519 keypair.
- `valid_perpetual.lic`: Valid perpetual `enterprise` license (5 seats, `machine: null`).
- `valid_expiring.lic`: Valid expiring `pro` license (`expires: 2099-01-01`).
- `expired.lic`: Expired `pro` license (`expires: 2020-01-01`).
- `machine_bound.lic`: Valid `team` license bound to `sha256("TEST-MACHINE")`.
- `tampered.lic`: Copy of `valid_perpetual.lic` with one character flipped in the payload segment.
- `fake_asset.bin`: Deterministic dummy binary referenced by the test update manifests.
- `manifest_ok.json` + `manifest_ok.json.sig`: Valid update manifest and Ed25519 signature over the exact raw bytes of `manifest_ok.json`.
- `manifest_badsig.json` + `manifest_badsig.json.sig`: Copy of `manifest_ok.json` with a flipped-byte signature in `.sig`.

## Generator Commands

Run from repository root:

```bash
# 1. Generate test-only keypair & public key
python3 tools/license/mcu_license.py keygen --out studio/tests/data/test_ed25519_private.pem
python3 tools/license/mcu_license.py pubkey --key studio/tests/data/test_ed25519_private.pem --out studio/tests/data/test_pubkey.b64

# 2. Generate license test vectors
python3 tools/license/mcu_license.py issue \
  --key studio/tests/data/test_ed25519_private.pem \
  --licensee "Test Perpetual User" --email "perpetual@example.com" \
  --edition enterprise --perpetual --machine none \
  --features agent,hil,updates --seats 5 --issued 2026-01-01 \
  --out studio/tests/data/valid_perpetual.lic

python3 tools/license/mcu_license.py issue \
  --key studio/tests/data/test_ed25519_private.pem \
  --licensee "Test Expiring User" --email "expiring@example.com" \
  --edition pro --expires 2099-01-01 --machine none \
  --features agent,hil,updates --seats 1 --issued 2026-01-01 \
  --out studio/tests/data/valid_expiring.lic

python3 tools/license/mcu_license.py issue \
  --key studio/tests/data/test_ed25519_private.pem \
  --licensee "Test Expired User" --email "expired@example.com" \
  --edition pro --expires 2020-01-01 --machine none \
  --features agent,hil --seats 1 --issued 2019-01-01 \
  --out studio/tests/data/expired.lic

python3 tools/license/mcu_license.py issue \
  --key studio/tests/data/test_ed25519_private.pem \
  --licensee "Test Machine Bound" --email "machine@example.com" \
  --edition team --expires 2099-01-01 --machine TEST-MACHINE \
  --features agent,hil,updates --seats 3 --issued 2026-01-01 \
  --out studio/tests/data/machine_bound.lic

# 3. Generate tampered.lic (flip 1 payload char from valid_perpetual.lic)
python3 -c '
from pathlib import Path
p, b, s = Path("studio/tests/data/valid_perpetual.lic").read_text().strip().split(".")
c = "B" if b[10] != "B" else "A"
Path("studio/tests/data/tampered.lic").write_text(f"{p}.{b[:10]}{c}{b[11:]}.{s}\n")
'

# 4. Generate dummy asset & update manifests
python3 -c '
from pathlib import Path
Path("studio/tests/data/fake_asset.bin").write_bytes(b"MCU-HARNESS-STUDIO-FAKE-ASSET-V1\x00\x01\x02\x03")
Path("/tmp/mcuh_notes_en.md").write_text("- Test release notes EN\n")
Path("/tmp/mcuh_notes_zh.md").write_text("- 测试发布说明 ZH\n")
'
python3 tools/release/make_update_manifest.py \
  --key studio/tests/data/test_ed25519_private.pem \
  --version 1.2.3 --channel stable \
  --notes-en /tmp/mcuh_notes_en.md --notes-zh /tmp/mcuh_notes_zh.md \
  --asset linux-x64=studio/tests/data/fake_asset.bin=https://example.invalid/mcu-harness-studio-1.2.3.tar.gz \
  --released 2026-01-01T00:00:00Z \
  --out studio/tests/data/manifest_ok.json

python3 -c '
import base64
from pathlib import Path
Path("studio/tests/data/manifest_badsig.json").write_bytes(Path("studio/tests/data/manifest_ok.json").read_bytes())
sig = bytearray(base64.b64decode(Path("studio/tests/data/manifest_ok.json.sig").read_text().strip()))
sig[0] ^= 0xFF
Path("studio/tests/data/manifest_badsig.json.sig").write_text(base64.b64encode(bytes(sig)).decode() + "\n")
'
```
