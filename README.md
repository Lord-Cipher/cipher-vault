# Cipher Vault

Cipher Vault is the private, encrypted snapshot repository for the Lord Cipher bot-hosting platform.

## Snapshot format

The hosting platform creates encrypted snapshots under `snapshots/<snapshot-id>/`. Each snapshot contains a compressed archive and a JSON manifest. `LATEST.json` is updated in the same Git commit as the snapshot and is published only after the archive and manifest blobs are prepared. This makes the Git reference the atomic publication point: readers see the previous complete snapshot or the new complete snapshot.

The encrypted archive includes the platform `storage/` and `sandbox/` state, including user profiles, referrals, wallets, plans, subscriptions, trial state, purchases, configuration, admin state, audit data, bot metadata, uploaded bot files, bot environment data, and custom assets. Runtime caches, temporary files, compiled Python files, logs, and previously published backup artifacts (`backups/`) are excluded.

The archive is encrypted with the deployment-only `CIPHER_VAULT_KEY`. Never commit that key, `CIPHER_VAULT_TOKEN`, bot tokens, or raw database files to this repository.

## Railway configuration

Configure these variables on the Railway service running the bot:

```text
CIPHER_VAULT_REPO=Lord-Cipher/cipher-vault
CIPHER_VAULT_BRANCH=main
CIPHER_VAULT_TOKEN=<GitHub token with private-repository contents access>
CIPHER_VAULT_KEY=<Fernet key generated for this deployment>
```

Generate a key once with Python and store it only in Railway:

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

The bot performs a scheduled sync every 30 minutes and exposes owner-only **Vault Management**, **Force Sync to Vault**, and **Vault History** controls in Telegram.

For VPS, Render, Docker, or other hosts that do not use a platform secret manager, copy `cipher_vault.json.example` from the bot-hosting repository to `cipher_vault.json` beside `bot.py`, fill in the values, and restrict the file to the service account (`chmod 600 cipher_vault.json`). The bot also accepts `CIPHER_VAULT_CONFIG=/path/to/cipher_vault.json`. Environment variables override file values, so the same code works with Railway variables, a VPS file, a Render secret, or a Docker secret.

## Recovery

To materialize the newest verified snapshot onto a new Railway or VPS instance, copy `materialize.py` to the new platform directory, install `cryptography`, set `CIPHER_VAULT_TOKEN` and `CIPHER_VAULT_KEY`, and run:

```bash
python materialize.py --target /path/to/cipher-bot-hosting
```

The utility verifies the archive checksum, authenticates/decrypts it, rejects unsafe archive paths, stages the result, and preserves the previous target as a pre-restore rollback directory before the final rename.

Improvements:

- Restores both published formats — `cipher-vault-v1` snapshots **and** `cipher-platform-full-state-v1` part archives (parts are reassembled and every part's SHA-256 is verified).
- Downloads archives through the Git Blobs API when a blob SHA is known, removing the 1 MB Contents API cap that silently broke larger snapshots.
- `--snapshot <id>` restores a specific snapshot instead of the latest one.
- Extraction rejects symlink/hardlink members and uses Python 3.12's `data` filter when available.
- `keys/` and secret files are now protected by `.gitignore` — per-file encryption keys must never be committed to this repository.
