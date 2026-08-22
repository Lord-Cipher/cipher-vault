#!/usr/bin/env python3
"""Materialize the latest encrypted Cipher Vault snapshot into a platform directory.

Usage:
  CIPHER_VAULT_KEY=... python materialize.py --target /opt/cipher-bot-hosting
"""
from __future__ import annotations
import argparse
import base64
import io
import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from cryptography.fernet import Fernet


def download(url: str, token: str = "") -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"} if token else {})
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="Existing or new platform directory")
    ap.add_argument("--repo", default=os.getenv("CIPHER_VAULT_REPO", "Lord-Cipher/cipher-vault"))
    ap.add_argument("--branch", default=os.getenv("CIPHER_VAULT_BRANCH", "main"))
    args = ap.parse_args()
    token = os.getenv("CIPHER_VAULT_TOKEN", os.getenv("GITHUB_TOKEN", ""))
    key = os.getenv("CIPHER_VAULT_KEY", "")
    if not key:
        raise SystemExit("CIPHER_VAULT_KEY is required")
    owner, name = args.repo.split("/", 1)
    api = f"https://api.github.com/repos/{owner}/{name}"
    latest_response = json.loads(download(f"{api}/contents/LATEST.json?ref={args.branch}", token))
    latest_doc = json.loads(base64.b64decode(latest_response["content"]))
    manifest_path = latest_doc["manifest"]
    manifest_response = json.loads(download(f"{api}/contents/{manifest_path}?ref={args.branch}", token))
    manifest_doc = json.loads(base64.b64decode(manifest_response["content"]))
    archive_response = json.loads(download(f"{api}/contents/{manifest_doc['archive']}?ref={args.branch}", token))
    archive = base64.b64decode(archive_response["content"])
    if __import__('hashlib').sha256(archive).hexdigest() != manifest_doc["archiveSha256"]:
        raise SystemExit("Archive checksum mismatch")
    plain = Fernet(key.encode()).decrypt(archive)
    target = Path(args.target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="cipher-vault-materialize-", dir=str(target.parent)))
    try:
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tf:
            for member in tf.getmembers():
                dest = (stage / member.name).resolve()
                if stage not in dest.parents:
                    raise SystemExit("Unsafe archive path")
            tf.extractall(stage)
        backup = target.with_name(target.name + ".before-restore")
        if target.exists():
            if backup.exists(): shutil.rmtree(backup)
            target.rename(backup)
        stage.rename(target)
        print(json.dumps({"ok": True, "snapshotId": manifest_doc["snapshotId"], "files": manifest_doc["fileCount"], "target": str(target)}, indent=2))
        return 0
    finally:
        if stage.exists(): shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
