#!/usr/bin/env python3
"""MCU Harness Studio 离线 Ed25519 许可证签名与校验工具。

子命令：
    keygen  --out <pem>
    pubkey  --key <pem> [--out <b64>]
    issue   --key <pem> --licensee <name> --email <addr> --edition pro|team|enterprise
            (--days N | --perpetual | --expires YYYY-MM-DD)
            [--machine <id|none>] [--features agent,hil,updates] [--seats 1]
            [--issued YYYY-MM-DD] [--out <licfile>]
    verify  --pub <b64|path> <licfile>
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_LICENSE_PREFIX = "MCUH1"
_VALID_EDITIONS = ("pro", "team", "enterprise")
_RE_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(segment: str) -> bytes:
    if not segment or not re.fullmatch(r"[A-Za-z0-9\-_]+", segment):
        raise ValueError("Invalid base64url segment")
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _write_private_pem(out_path: Path, pem_bytes: bytes) -> Path:
    resolved = out_path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(resolved), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem_bytes)
    finally:
        try:
            os.chmod(resolved, 0o600)
        except OSError:
            pass
    return resolved


def load_private_key(pem_path: Path | str) -> Ed25519PrivateKey:
    """从 PKCS8 PEM 文件加载 Ed25519 私钥。"""
    raw = Path(pem_path).expanduser().read_bytes()
    key = serialization.load_pem_private_key(raw, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"Expected Ed25519 private key in {pem_path}, got {type(key).__name__}")
    return key


def extract_pubkey_b64(key: Ed25519PrivateKey) -> str:
    """提取 32 字节原始 Ed25519 公钥并编码为标准 Base64 字符串。"""
    raw_pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw_pub).decode("ascii")


def load_public_key_b64(pub_or_path: str) -> Ed25519PublicKey:
    """从 Base64 字符串或 `.b64` 文件路径加载 Ed25519 公钥。"""
    cand = Path(pub_or_path).expanduser()
    b64_text = cand.read_text(encoding="utf-8").strip() if cand.is_file() else pub_or_path.strip()
    raw_pub = base64.b64decode(b64_text, validate=True)
    if len(raw_pub) != 32:
        raise ValueError(f"Expected 32-byte Ed25519 public key, got {len(raw_pub)} bytes")
    return Ed25519PublicKey.from_public_bytes(raw_pub)


def normalize_machine_hash(machine: str | None) -> str | None:
    """将机器标识归一化为小写 SHA-256 十六进制字符串，或返回 None。"""
    if machine is None:
        return None
    cleaned = machine.strip()
    if not cleaned or cleaned.lower() == "none":
        return None
    if _RE_SHA256_HEX.match(cleaned):
        return cleaned.lower()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def issue_license_token(
    priv_key: Ed25519PrivateKey,
    *,
    licensee: str,
    email: str,
    edition: str,
    days: int | None = None,
    perpetual: bool = False,
    expires: str | None = None,
    issued: str | None = None,
    machine: str | None = None,
    features: list[str] | None = None,
    seats: int = 1,
    license_id: str | None = None,
) -> str:
    """签发单行 `MCUH1.<b64url(payload)>.<b64url(sig)>` 许可证令牌。"""
    if edition not in _VALID_EDITIONS:
        raise ValueError(f"Unsupported edition {edition!r}; expected one of {_VALID_EDITIONS}")
    if seats < 1:
        raise ValueError(f"seats must be >= 1, got {seats}")

    issued_date = date.fromisoformat(issued) if issued else date.today()
    if perpetual:
        expires_str: str | None = None
    elif expires is not None:
        expires_str = date.fromisoformat(expires).isoformat()
    elif days is not None:
        expires_str = (issued_date + timedelta(days=days)).isoformat()
    else:
        raise ValueError("One of days, perpetual, or expires must be provided")

    feat_list = [f.strip() for f in (features or ["agent", "hil", "updates"]) if f.strip()]
    payload: dict[str, Any] = {
        "v": 1,
        "license_id": license_id or str(uuid.uuid4()),
        "licensee": licensee,
        "email": email,
        "edition": edition,
        "seats": int(seats),
        "issued": issued_date.isoformat(),
        "expires": expires_str,
        "machine": normalize_machine_hash(machine),
        "features": feat_list,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    sig_bytes = priv_key.sign(payload_bytes)
    return f"{_LICENSE_PREFIX}.{_b64url_encode(payload_bytes)}.{_b64url_encode(sig_bytes)}"


def verify_license_token(
    token: str,
    pub_key: Ed25519PublicKey,
    *,
    today: date | None = None,
    machine_id: str | None = None,
) -> dict[str, Any]:
    """校验许可证签名、格式、有效期及可选机器绑定，成功返回 payload 字典。"""
    stripped = token.strip()
    parts = stripped.split(".")
    if len(parts) != 3 or parts[0] != _LICENSE_PREFIX:
        raise ValueError("Malformed license format (expected MCUH1.<payload>.<sig>)")

    payload_bytes = _b64url_decode(parts[1])
    sig_bytes = _b64url_decode(parts[2])
    if len(sig_bytes) != 64:
        raise ValueError(f"Invalid Ed25519 signature length: {len(sig_bytes)}")

    try:
        pub_key.verify(sig_bytes, payload_bytes)
    except InvalidSignature as exc:
        raise ValueError("Invalid Ed25519 license signature") from exc

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid license payload JSON: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("Unsupported license payload version")

    exp_raw = payload.get("expires")
    if exp_raw is not None:
        exp_date = date.fromisoformat(str(exp_raw))
        ref_date = today or date.today()
        if exp_date < ref_date:
            raise ValueError(f"License expired on {exp_date.isoformat()}")

    bound_machine = payload.get("machine")
    if bound_machine is not None and machine_id is not None:
        expected_hash = normalize_machine_hash(machine_id)
        if expected_hash != bound_machine:
            raise ValueError("License machine hash mismatch")

    return payload


def _cmd_keygen(args: argparse.Namespace) -> int:
    priv = Ed25519PrivateKey.generate()
    pem_bytes = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    saved = _write_private_pem(Path(args.out), pem_bytes)
    print(str(saved))
    return 0


def _cmd_pubkey(args: argparse.Namespace) -> int:
    priv = load_private_key(args.key)
    b64 = extract_pubkey_b64(priv)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(b64 + "\n", encoding="utf-8")
    print(b64)
    return 0


def _cmd_issue(args: argparse.Namespace) -> int:
    priv = load_private_key(args.key)
    features = [f.strip() for f in args.features.split(",") if f.strip()]
    token = issue_license_token(
        priv,
        licensee=args.licensee,
        email=args.email,
        edition=args.edition,
        days=args.days,
        perpetual=args.perpetual,
        expires=args.expires,
        issued=args.issued,
        machine=args.machine,
        features=features,
        seats=args.seats,
    )
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(token + "\n", encoding="utf-8")
    else:
        print(token)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        pub = load_public_key_b64(args.pub)
        token = Path(args.licfile).expanduser().read_text(encoding="utf-8")
        payload = verify_license_token(token, pub, machine_id=args.machine)
    except Exception as exc:
        sys.stderr.write(f"License verification failed: {exc}\n")
        return 1
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcu_license",
        description="MCU Harness Studio Ed25519 离线许可证工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_kg = sub.add_parser("keygen", help="生成 Ed25519 私钥 PEM (0o600)")
    p_kg.add_argument("--out", required=True, help="输出私钥 PEM 路径")
    p_kg.set_defaults(func=_cmd_keygen)

    p_pk = sub.add_parser("pubkey", help="导出 Base64 编码的 32B Ed25519 公钥")
    p_pk.add_argument("--key", required=True, help="私钥 PEM 路径")
    p_pk.add_argument("--out", default=None, help="可选：写入 .b64 文件路径")
    p_pk.set_defaults(func=_cmd_pubkey)

    p_is = sub.add_parser("issue", help="签发 MCUH1 离线许可证")
    p_is.add_argument("--key", required=True, help="私钥 PEM 路径")
    p_is.add_argument("--licensee", required=True, help="被许可方名称")
    p_is.add_argument("--email", required=True, help="被许可方邮箱")
    p_is.add_argument("--edition", required=True, choices=list(_VALID_EDITIONS), help="版本")
    exp_grp = p_is.add_mutually_exclusive_group(required=True)
    exp_grp.add_argument("--days", type=int, help="有效天数")
    exp_grp.add_argument("--perpetual", action="store_true", help="永久许可")
    exp_grp.add_argument("--expires", default=None, help="显式过期日期 (YYYY-MM-DD)")
    p_is.add_argument("--issued", default=None, help="签发日期 (YYYY-MM-DD，默认当天)")
    p_is.add_argument("--machine", default="none", help="机器 ID 或 'none'")
    p_is.add_argument("--features", default="agent,hil,updates", help="逗号分隔的功能集")
    p_is.add_argument("--seats", type=int, default=1, help="席位数（默认 1）")
    p_is.add_argument("--out", default=None, help="输出许可证文件路径（缺省打印到 stdout）")
    p_is.set_defaults(func=_cmd_issue)

    p_vf = sub.add_parser("verify", help="校验离线许可证文件")
    p_vf.add_argument("--pub", required=True, help="Base64 公钥或 .b64 文件路径")
    p_vf.add_argument("--machine", default=None, help="可选：待核对的机器 ID")
    p_vf.add_argument("licfile", help="许可证文件路径")
    p_vf.set_defaults(func=_cmd_verify)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
