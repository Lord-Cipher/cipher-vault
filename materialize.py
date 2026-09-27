#!/usr/bin/env python3
"""Materialize a Cipher Vault snapshot into a platform directory.

Restores the newest published snapshot (LATEST.json) or a specific snapshot
id. Both published formats are supported:

- cipher-vault-v1              snapshots/<id>/{platform.tar.gz.enc, manifest.json}
- cipher-platform-full-state-v1 backups/full_state/<id>/{manifest.json, part-*.bin}

Large archives are downloaded through the Git Blobs API (the Contents API
caps file responses at 1 MB, which silently breaks bigger snapshots).

Usage:
  CIPHER_VAULT_KEY=... python materialize.py --target /opt/cipher-bot-hosting
  CIPHER_VAULT_KEY=... python materialize.py --target ... --snapshot 20260908T102259Z-0d6ad122d181
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional
from cryptography.fernet import Fernet

FULL_STATE_FORMAT = "cipher-platform-full-state-v1"
_VAULT_UA = "cipher-vault-materialize"


def _headers(token: str, raw: bool = False) -> Dict[str, str]:
    headers = {
        "User-Agent": _VAULT_UA,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if raw:
        # Raw media type works for files between 1 and 100 MB.
        headers["Accept"] = "application/vnd.github.raw"
    else:
        headers["Accept"] = "application/vnd.github+json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def download(url: str, token: str = "", raw: bool = False) -> bytes:
    req = urllib.request.Request(url, headers=_headers(token, raw))
    with urllib.request.urlopen(req, timeout=300) as response:
        return response.read()


def fetch_json(api: str, path: str, ref: str, token: str) -> Dict[str, Any]:
    url = f"{api}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}"
    return json.loads(download(url, token))


def fetch_blob(api: str, path: str, blob_sha: str, ref: str, token: str) -> bytes:
    """Fetch file bytes via the Git Blobs API when the SHA is known (no 1 MB
    cap); fall back to the Contents API, then to raw media type."""
    if blob_sha:
        data = json.loads(download(f"{api}/git/blobs/{blob_sha}", token))
        if data.get("encoding") == "base64":
            return base64.b64decode(data.get("content") or "")
        raise SystemExit("Unexpected blob encoding from the GitHub API")
    quoted = urllib.parse.quote(path)
    try:
        data = json.loads(download(f"{api}/contents/{quoted}?ref={urllib.parse.quote(ref)}", token))
        content = data.get("content")
        if content:
            return base64.b64decode(content)
    except Exception:
        pass
    return download(f"{api}/contents/{quoted}?ref={urllib.parse.quote(ref)}", token, raw=True)


def restore_full_state(api: str, manifest_path: str, manifest: Dict[str, Any],
                       ref: str, token: str) -> bytes:
    """Reassemble the encrypted archive from a full-state parts manifest."""
    parts = manifest.get("parts") or []
    if not parts:
        raise SystemExit("Full-state manifest has no parts")
    chunks = []
    for part in parts:
        rel = str(part.get("path") or "")
        expected = str(part.get("sha256") or "")
        data = download(f"{api}/contents/{urllib.parse.quote(rel)}?ref={urllib.parse.quote(ref)}",
                        token, raw=True)
        if expected and hashlib.sha256(data).hexdigest() != expected:
            raise SystemExit(f"Part checksum mismatch: {rel}")
        chunks.append(data)
    archive = b"".join(chunks)
    total_sha = str(manifest.get("archiveSha256") or "")
    if total_sha and hashlib.sha256(archive).hexdigest() != total_sha:
        raise SystemExit("Reassembled archive checksum mismatch")
    return archive


def safe_extract(plain: bytes, stage: Path) -> None:
    """Extract with path-traversal and special-member protection."""
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tf:
        for member in tf.getmembers():
            dest = (stage / member.name).resolve()
            if stage.resolve() not in dest.parents and dest != stage.resolve():
                raise SystemExit("Unsafe archive path")
            if member.issym() or member.islnk():
                raise SystemExit(f"Unsafe archive member (link): {member.name}")
        try:
            tf.extractall(stage, filter="data")  # Python 3.12+: strips unsafe metadata
        except TypeError:
            tf.extractall(stage)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="Existing or new platform directory")
    ap.add_argument("--repo", default=os.getenv("CIPHER_VAULT_REPO", "Lord-Cipher/cipher-vault"))
    ap.add_argument("--branch", default=os.getenv("CIPHER_VAULT_BRANCH", "main"))
    ap.add_argument("--snapshot", default="", help="Restore a specific snapshot id instead of the latest")
    args = ap.parse_args()
    token = os.getenv("CIPHER_VAULT_TOKEN", os.getenv("GITHUB_TOKEN", ""))
    key = os.getenv("CIPHER_VAULT_KEY", "")
    if not key:
        raise SystemExit("CIPHER_VAULT_KEY is required")
    if "/" not in args.repo:
        raise SystemExit("CIPHER_VAULT_REPO must be an owner/name pair")
    owner, name = args.repo.split("/", 1)
    api = f"https://api.github.com/repos/{owner}/{name}"

    if args.snapshot:
        manifest_path = f"snapshots/{args.snapshot}/manifest.json"
        snapshot_id = args.snapshot
        manifest_doc = json.loads(download(
            f"{api}/contents/{urllib.parse.quote(manifest_path)}?ref={urllib.parse.quote(args.branch)}",
            token))
        if isinstance(manifest_doc.get("content"), str) and manifest_doc.get("encoding") == "base64":
            manifest_doc = json.loads(base64.b64decode(manifest_doc["content"]))
    else:
        latest_doc = json.loads(base64.b64decode(
            fetch_json(api, "LATEST.json", args.branch, token)["content"]))
        manifest_path = latest_doc["manifest"]
        snapshot_id = str(latest_doc.get("snapshotId") or "")
        manifest_doc = json.loads(base64.b64decode(
            fetch_json(api, manifest_path, args.branch, token)["content"]))

    fmt = str(manifest_doc.get("format") or "cipher-vault-v1")
    if fmt == FULL_STATE_FORMAT:
        encrypted = restore_full_state(api, manifest_path, manifest_doc, args.branch, token)
    else:
        encrypted = fetch_blob(api, str(manifest_doc["archive"]),
                               str(manifest_doc.get("archiveBlobSha") or ""),
                               args.branch, token)
        expected = str(manifest_doc.get("archiveSha256") or "")
        if expected and hashlib.sha256(encrypted).hexdigest() != expected:
            raise SystemExit("Archive checksum mismatch")

    plain = Fernet(key.encode()).decrypt(encrypted)
    target = Path(args.target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="cipher-vault-materialize-", dir=str(target.parent)))
    try:
        safe_extract(plain, stage)
        backup = target.with_name(target.name + ".before-restore")
        if target.exists():
            if backup.exists():
                shutil.rmtree(backup)
            target.rename(backup)
        stage.rename(target)
        print(json.dumps({
            "ok": True,
            "snapshotId": str(manifest_doc.get("snapshotId") or snapshot_id),
            "format": fmt,
            "files": manifest_doc.get("fileCount"),
            "target": str(target),
        }, indent=2))
        return 0
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
