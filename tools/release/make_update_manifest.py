#!/usr/bin/env python3
"""MCU Harness Studio 更新清单（manifest.json）生成与 Ed25519 签名工具。

用法：
    make_update_manifest.py --key <pem> --version X.Y.Z [--channel stable]
        --notes-en notes_en.md --notes-zh notes_zh.md
        --asset <key>=<local_path>=<download_url> [...]
        [--min-supported 1.0.0] [--released ISO8601]
        --out manifest.json

将生成 `manifest.json` 与 `manifest.json.sig`（对 `manifest.json` 原始字节的 Base64 Ed25519 签名）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _load_private_key(pem_path: Path | str) -> Ed25519PrivateKey:
    raw = Path(pem_path).expanduser().read_bytes()
    key = serialization.load_pem_private_key(raw, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"Expected Ed25519 private key in {pem_path}, got {type(key).__name__}")
    return key


def _sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _parse_asset_spec(spec: str) -> tuple[str, Path, str]:
    parts = spec.split("=", 2)
    if len(parts) != 3 or not parts[0].strip() or not parts[1].strip() or not parts[2].strip():
        raise ValueError(f"Invalid --asset spec {spec!r}; expected key=<path>=<url>")
    return parts[0].strip(), Path(parts[1].strip()).expanduser(), parts[2].strip()


def generate_manifest(
    *,
    key_path: Path | str,
    version: str,
    channel: str = "stable",
    notes_en_path: Path | str,
    notes_zh_path: Path | str,
    asset_specs: Sequence[str],
    out_path: Path | str,
    min_supported: str = "1.0.0",
    released: str | None = None,
) -> tuple[Path, Path]:
    """生成更新清单 JSON 与 `.sig` 签名文件，返回 `(manifest_path, sig_path)`。"""
    priv = _load_private_key(key_path)

    notes_en = Path(notes_en_path).expanduser().read_text(encoding="utf-8")
    notes_zh = Path(notes_zh_path).expanduser().read_text(encoding="utf-8")

    assets: dict[str, dict[str, Any]] = {}
    for spec in asset_specs:
        asset_key, asset_path, asset_url = _parse_asset_spec(spec)
        if not asset_path.is_file():
            raise FileNotFoundError(f"Asset file not found: {asset_path}")
        digest, size = _sha256_file(asset_path)
        assets[asset_key] = {
            "url": asset_url,
            "sha256": digest,
            "size": size,
        }

    released_ts = released or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_obj: dict[str, Any] = {
        "schema": 1,
        "channel": channel,
        "version": version,
        "released": released_ts,
        "notes": {
            "en": notes_en,
            "zh_CN": notes_zh,
        },
        "min_supported": min_supported,
        "assets": assets,
    }

    manifest_path = Path(out_path).expanduser().resolve()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = (json.dumps(manifest_obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    manifest_path.write_bytes(raw_bytes)

    sig_b64 = base64.b64encode(priv.sign(raw_bytes)).decode("ascii")
    sig_path = Path(f"{manifest_path}.sig")
    sig_path.write_text(sig_b64 + "\n", encoding="utf-8")
    return manifest_path, sig_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make_update_manifest",
        description="生成并签名 MCU Harness Studio 更新清单（manifest.json + manifest.json.sig）",
    )
    parser.add_argument("--key", required=True, help="Ed25519 私钥 PEM 路径")
    parser.add_argument("--version", required=True, help="发布版本号，例如 1.0.0")
    parser.add_argument("--channel", default="stable", help="发布渠道（默认 stable）")
    parser.add_argument("--notes-en", required=True, help="英文发布说明 Markdown 文件路径")
    parser.add_argument("--notes-zh", required=True, help="中文发布说明 Markdown 文件路径")
    parser.add_argument(
        "--asset",
        action="append",
        required=True,
        help="资产条目，格式为 key=<path>=<url>（可多次指定）",
    )
    parser.add_argument("--min-supported", default="1.0.0", help="最低支持版本（默认 1.0.0）")
    parser.add_argument("--released", default=None, help="ISO-8601 发布时间（默认当前 UTC）")
    parser.add_argument("--out", required=True, help="输出 manifest.json 路径")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    manifest_path, sig_path = generate_manifest(
        key_path=args.key,
        version=args.version,
        channel=args.channel,
        notes_en_path=args.notes_en,
        notes_zh_path=args.notes_zh,
        asset_specs=args.asset,
        out_path=args.out,
        min_supported=args.min_supported,
        released=args.released,
    )
    print(f"Wrote manifest: {manifest_path}")
    print(f"Wrote signature: {sig_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
