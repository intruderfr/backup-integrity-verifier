# backup-integrity-verifier

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-beta-green)](https://github.com/intruderfr/backup-integrity-verifier)

A small, dependency-free Python CLI that turns "did the backup job actually finish in a usable state?" from a manual smoke-test into a repeatable, auditable procedure.

Most backup jobs silently succeed even when their output is garbage — truncated dumps, corrupted archives, or tampered files. `backup-integrity-verifier` validates the archive against a declarative **manifest**, logs every run to a local **SQLite history DB**, and produces an audit-ready **JSON/HTML report** you can hand to your auditor or paste into a change ticket.

Designed for Business Continuity & DR teams who need to prove — not assume — that backups are restorable.

---

## Features

- **Manifest-driven validation.** Declare the expected contents of an archive once (file list, sizes, SHA-256 checksums); verify every subsequent backup against it.
- **Multiple archive formats.** `.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.zip`.
- **Optional test-restore.** Extract to a temp directory inside a path-traversal-guarded sandbox to prove the archive is not just intact but actually restorable.
- **Status categories.** Each member is classified as `ok`, `checksum_mismatch`, `size_mismatch`, `missing`, or `unexpected` — matching the language your compliance framework already uses.
- **Persistent history.** Every run is appended to a local SQLite DB so you can answer "when did this archive last verify cleanly?" in one query.
- **Audit reports.** Produce both a machine-readable JSON report and a human-readable HTML report in a single command.
- **Zero runtime dependencies.** Pure Python 3.9+ standard library. No install pain on servers or in minimal containers.

---

## Installation

```bash
# From source
git clone https://github.com/intruderfr/backup-integrity-verifier.git
cd backup-integrity-verifier
pip install -e .

# Or without installing, run as a module
python -m backup_verifier --help
```

Requires Python 3.9+.

---

## Quick start

### 1. Build a manifest from your source tree

Before you make your first backup, hash the source directory to pin the expected state:

```bash
backup-verifier build-manifest ./billing-data \
    --archive-name "prod-billing-nightly" \
    --note "Nightly dump of the billing DB" \
    -o manifest.json
```

This produces a `manifest.json` with every file, its size, and its SHA-256 digest.

### 2. Verify an archive against the manifest

```bash
backup-verifier verify ./backups/prod-billing-2026-04-19.tar.gz \
    --manifest manifest.json \
    --test-restore \
    --json  reports/2026-04-19.json \
    --html  reports/2026-04-19.html
```

What this does:

1. Hashes the archive itself (for chain-of-custody in the report).
2. Streams every file inside the archive and compares it against the manifest.
3. Extracts the archive to a temp directory to prove it is restorable.
4. Writes JSON and HTML reports.
5. Logs the run to `~/.backup_verifier/history.sqlite3`.
6. Exits 0 on PASS, 1 on FAIL — drop-in friendly for cron / systemd timers / CI.

### 3. Inspect history

```bash
backup-verifier history --limit 10
backup-verifier history --stats
```

---

## Exit codes

| Code | Meaning                                                              |
|------|----------------------------------------------------------------------|
| 0    | Archive verified successfully.                                       |
| 1    | One or more members mismatched, missing, or restore test failed.     |
| 2    | CLI / input error (bad path, malformed manifest, etc.).              |

---

## Typical integrations

### Nightly cron

```cron
30 3 * * * /usr/local/bin/backup-verifier verify /srv/backups/db-$(date +\%F).tar.gz \
    --manifest /etc/biv/db.manifest.json \
    --test-restore \
    --json /var/log/biv/db-$(date +\%F).json \
    || /usr/local/bin/pager-trigger "DB backup integrity FAILED"
```

### GitHub Actions post-backup job

```yaml
- name: Verify backup integrity
  run: |
    pip install backup-integrity-verifier
    backup-verifier verify ./backup.tar.gz \
      --manifest ./manifest.json --test-restore \
      --json backup-report.json
```

### Python API

```python
from backup_verifier import ArchiveVerifier, Manifest, ReportBuilder

manifest = Manifest.from_json("manifest.json")
result = ArchiveVerifier(manifest, test_restore=True).verify("backup.tar.gz")

if not result.ok:
    for bad in result.failed_members():
        print(bad.status, bad.path, bad.detail)

ReportBuilder(result).write_html("report.html")
```

---

## Manifest format

```json
{
  "archive_name": "prod-db-nightly",
  "created_utc": "2026-04-19T02:00:00+00:00",
  "notes": "Nightly PostgreSQL dump for the billing database.",
  "entries": [
    {
      "path": "billing/dump.sql",
      "sha256": "…hex…",
      "size": 10485760,
      "required": true
    }
  ]
}
```

- `path` is a forward-slash path relative to the archive root.
- `required: false` members are allowed to be missing from the archive; everything else is flagged.

---

## Running the tests

```bash
pip install -e ".[dev]"
pytest -q
```

The test suite covers happy paths, tampered / missing / unexpected files, test-restore, report rendering, and history persistence.

---

## Design notes

- **No runtime dependencies** — deliberate. This tool is meant to be dropped onto hardened backup servers where installing extras is friction.
- **Streaming hashes** — archive members are read in 1 MiB chunks; you can verify multi-GB archives without exploding RAM.
- **Path-traversal guard** — the test-restore path refuses any archive member whose resolved path escapes the temp directory (classic zip-slip defense).
- **Append-only history** — SQLite is chosen because it's already on every Unix host and needs no daemon. The schema is intentionally flat so `sqlite3 history.sqlite3 '.dump'` produces something your auditor can read.

---

## Roadmap

- [ ] PGP signature verification for the manifest itself.
- [ ] Pluggable remote-object-store backends (S3, Azure Blob, GCS).
- [ ] Prometheus text-file exporter (`backup_verifier_last_ok{archive="…"} 1`).
- [ ] `--since` and `--archive` filters on `history`.

---

## License

[MIT](LICENSE) — see license file for full text.

---

## Author

**Aslam Ahamed** — Head of IT @ Prestige One Developments, Dubai
[LinkedIn](https://www.linkedin.com/in/aslam-ahamed/) · [GitHub](https://github.com/intruderfr)
