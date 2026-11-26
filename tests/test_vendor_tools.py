"""Tests for vendor licensing (`mcu_license.py`), update manifest signing (`make_update_manifest.py`),
and Studio Ed25519 test vectors & public keys.
"""

from __future__ import annotations

import base64
import hashlib
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LICENSE_TOOL = _REPO_ROOT / "tools" / "license" / "mcu_license.py"
_MANIFEST_TOOL = _REPO_ROOT / "tools" / "release" / "make_update_manifest.py"
_KEYS_DIR = _REPO_ROOT / "studio" / "keys"
_TEST_DATA_DIR = _REPO_ROOT / "studio" / "tests" / "data"


class TestVendorToolsAndVectors(unittest.TestCase):
    def test_studio_keys_exist_and_are_valid_ed25519_pubkeys(self) -> None:
        for name in ("license_pubkey.b64", "update_pubkey.b64"):
            path = _KEYS_DIR / name
            self.assertTrue(path.is_file(), msg=f"Missing {path}")
            raw = base64.b64decode(path.read_text(encoding="utf-8").strip(), validate=True)
            self.assertEqual(len(raw), 32)
            Ed25519PublicKey.from_public_bytes(raw)

    def test_license_cli_roundtrip_keygen_pubkey_issue_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pem = tmp_dir / "vendor.pem"
            pub_b64 = tmp_dir / "vendor.b64"
            lic = tmp_dir / "user.lic"

            rc_kg = subprocess.run(
                [sys.executable, str(_LICENSE_TOOL), "keygen", "--out", str(pem)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rc_kg.returncode, 0, msg=rc_kg.stderr)
            self.assertTrue(pem.is_file())
            self.assertEqual(stat.S_IMODE(pem.stat().st_mode), 0o600)

            rc_pk = subprocess.run(
                [sys.executable, str(_LICENSE_TOOL), "pubkey", "--key", str(pem), "--out", str(pub_b64)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rc_pk.returncode, 0, msg=rc_pk.stderr)
            self.assertTrue(pub_b64.is_file())
            self.assertEqual(rc_pk.stdout.strip(), pub_b64.read_text(encoding="utf-8").strip())

            rc_is = subprocess.run(
                [
                    sys.executable,
                    str(_LICENSE_TOOL),
                    "issue",
                    "--key",
                    str(pem),
                    "--licensee",
                    "Acme Embedded",
                    "--email",
                    "ops@acme.invalid",
                    "--edition",
                    "team",
                    "--days",
                    "30",
                    "--machine",
                    "WORKSTATION-42",
                    "--features",
                    "agent,hil,updates",
                    "--seats",
                    "4",
                    "--out",
                    str(lic),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rc_is.returncode, 0, msg=rc_is.stderr)
            token = lic.read_text(encoding="utf-8").strip()
            self.assertTrue(token.startswith("MCUH1."))
            self.assertNotIn("=", token)

            rc_vf = subprocess.run(
                [
                    sys.executable,
                    str(_LICENSE_TOOL),
                    "verify",
                    "--pub",
                    pub_b64.read_text(encoding="utf-8").strip(),
                    "--machine",
                    "WORKSTATION-42",
                    str(lic),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rc_vf.returncode, 0, msg=rc_vf.stderr)
            payload = json.loads(rc_vf.stdout)
            self.assertEqual(payload["v"], 1)
            self.assertEqual(payload["edition"], "team")
            self.assertEqual(payload["seats"], 4)
            self.assertEqual(payload["machine"], hashlib.sha256(b"WORKSTATION-42").hexdigest())

    def test_update_manifest_cli_generates_valid_signed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pem = tmp_dir / "upd.pem"
            pub_b64 = tmp_dir / "upd.b64"
            notes_en = tmp_dir / "en.md"
            notes_zh = tmp_dir / "zh.md"
            asset = tmp_dir / "studio.tar.gz"
            out_manifest = tmp_dir / "manifest.json"

            subprocess.run([sys.executable, str(_LICENSE_TOOL), "keygen", "--out", str(pem)], check=True)
            subprocess.run(
                [sys.executable, str(_LICENSE_TOOL), "pubkey", "--key", str(pem), "--out", str(pub_b64)],
                check=True,
            )
            notes_en.write_text("# Release 2.0.0\n", encoding="utf-8")
            notes_zh.write_text("# 发布 2.0.0\n", encoding="utf-8")
            asset.write_bytes(b"binary-release-payload-123456")

            rc = subprocess.run(
                [
                    sys.executable,
                    str(_MANIFEST_TOOL),
                    "--key",
                    str(pem),
                    "--version",
                    "2.0.0",
                    "--channel",
                    "stable",
                    "--notes-en",
                    str(notes_en),
                    "--notes-zh",
                    str(notes_zh),
                    "--asset",
                    f"linux-x64={asset}=https://example.invalid/studio-2.0.0.tar.gz",
                    "--out",
                    str(out_manifest),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rc.returncode, 0, msg=rc.stderr)
            sig_file = tmp_dir / "manifest.json.sig"
            self.assertTrue(out_manifest.is_file())
            self.assertTrue(sig_file.is_file())

            raw_manifest = out_manifest.read_bytes()
            parsed = json.loads(raw_manifest.decode("utf-8"))
            self.assertEqual(parsed["schema"], 1)
            self.assertEqual(parsed["version"], "2.0.0")
            self.assertEqual(parsed["assets"]["linux-x64"]["size"], len(b"binary-release-payload-123456"))
            self.assertEqual(
                parsed["assets"]["linux-x64"]["sha256"],
                hashlib.sha256(b"binary-release-payload-123456").hexdigest(),
            )

            pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64.read_text(encoding="utf-8").strip()))
            sig = base64.b64decode(sig_file.read_text(encoding="utf-8").strip())
            pub.verify(sig, raw_manifest)

    def test_pregenerated_studio_test_vectors(self) -> None:
        pub_file = _TEST_DATA_DIR / "test_pubkey.b64"
        self.assertTrue(pub_file.is_file())
        pub_b64 = pub_file.read_text(encoding="utf-8").strip()
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64, validate=True))

        # Valid licenses
        for fname in ("valid_perpetual.lic", "valid_expiring.lic"):
            rc = subprocess.run(
                [sys.executable, str(_LICENSE_TOOL), "verify", "--pub", pub_b64, str(_TEST_DATA_DIR / fname)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rc.returncode, 0, msg=f"{fname}: {rc.stderr}")

        # Machine-bound license matches TEST-MACHINE, rejects OTHER
        rc_mb_ok = subprocess.run(
            [
                sys.executable,
                str(_LICENSE_TOOL),
                "verify",
                "--pub",
                pub_b64,
                "--machine",
                "TEST-MACHINE",
                str(_TEST_DATA_DIR / "machine_bound.lic"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(rc_mb_ok.returncode, 0, msg=rc_mb_ok.stderr)

        rc_mb_bad = subprocess.run(
            [
                sys.executable,
                str(_LICENSE_TOOL),
                "verify",
                "--pub",
                pub_b64,
                "--machine",
                "WRONG-MACHINE",
                str(_TEST_DATA_DIR / "machine_bound.lic"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(rc_mb_bad.returncode, 0)

        # Expired & tampered fail verification
        for bad_lic in ("expired.lic", "tampered.lic"):
            rc_bad = subprocess.run(
                [sys.executable, str(_LICENSE_TOOL), "verify", "--pub", pub_b64, str(_TEST_DATA_DIR / bad_lic)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rc_bad.returncode, 0, msg=f"{bad_lic} unexpectedly passed")

        # Update manifest vectors
        ok_bytes = (_TEST_DATA_DIR / "manifest_ok.json").read_bytes()
        ok_sig = base64.b64decode((_TEST_DATA_DIR / "manifest_ok.json.sig").read_text(encoding="utf-8").strip())
        pub.verify(ok_sig, ok_bytes)

        ok_obj = json.loads(ok_bytes.decode("utf-8"))
        asset_bytes = (_TEST_DATA_DIR / "fake_asset.bin").read_bytes()
        self.assertEqual(ok_obj["assets"]["linux-x64"]["size"], len(asset_bytes))
        self.assertEqual(ok_obj["assets"]["linux-x64"]["sha256"], hashlib.sha256(asset_bytes).hexdigest())

        bad_bytes = (_TEST_DATA_DIR / "manifest_badsig.json").read_bytes()
        bad_sig = base64.b64decode((_TEST_DATA_DIR / "manifest_badsig.json.sig").read_text(encoding="utf-8").strip())
        with self.assertRaises(InvalidSignature):
            pub.verify(bad_sig, bad_bytes)


if __name__ == "__main__":
    unittest.main()
