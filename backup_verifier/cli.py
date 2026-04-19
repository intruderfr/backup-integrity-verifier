"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .manifest import Manifest
from .report import ReportBuilder
from .storage import VerificationHistory
from .verifier import ArchiveVerifier


DEFAULT_DB = Path.home() / ".backup_verifier" / "history.sqlite3"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backup-verifier",
        description=(
            "Validate backup archives against a manifest, run optional test-restore, "
            "log every run to a local history DB, and emit JSON/HTML reports."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # build-manifest
    bm = sub.add_parser(
        "build-manifest",
        help="Build a manifest.json from a directory of source files.",
    )
    bm.add_argument("source_dir", type=Path, help="Directory to hash.")
    bm.add_argument(
        "-o", "--output", type=Path, default=Path("manifest.json"),
        help="Where to write the manifest (default: ./manifest.json).",
    )
    bm.add_argument(
        "--archive-name", default="",
        help="Logical archive name recorded in the manifest.",
    )
    bm.add_argument(
        "--note", default=None,
        help="Free-text note stored with the manifest.",
    )

    # verify
    v = sub.add_parser("verify", help="Verify an archive against a manifest.")
    v.add_argument("archive", type=Path, help="Path to .tar/.tar.gz/.zip archive.")
    v.add_argument("-m", "--manifest", type=Path, required=True)
    v.add_argument(
        "--test-restore", action="store_true",
        help="Additionally extract the archive to a temp dir to prove restorability.",
    )
    v.add_argument(
        "--json", type=Path, default=None,
        help="Write a JSON report to this path.",
    )
    v.add_argument(
        "--html", type=Path, default=None,
        help="Write an HTML report to this path.",
    )
    v.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"SQLite history DB (default: {DEFAULT_DB}).",
    )
    v.add_argument(
        "--no-history", action="store_true",
        help="Do not log this run to the history DB.",
    )
    v.add_argument(
        "--quiet", action="store_true", help="Suppress per-member console output.",
    )

    # history
    h = sub.add_parser("history", help="Inspect the local verification history.")
    h.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"SQLite history DB (default: {DEFAULT_DB}).",
    )
    h.add_argument("--limit", type=int, default=20)
    h.add_argument("--stats", action="store_true", help="Show aggregate stats only.")

    return p


def _cmd_build_manifest(args: argparse.Namespace) -> int:
    source = args.source_dir
    if not source.is_dir():
        print(f"ERROR: {source} is not a directory", file=sys.stderr)
        return 2
    now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    name = args.archive_name or source.name
    manifest = Manifest.build_from_directory(source, name, now)
    manifest.notes = args.note
    manifest.to_json(args.output)
    print(f"Wrote manifest with {len(manifest.entries)} entries -> {args.output}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    manifest = Manifest.from_json(args.manifest)
    verifier = ArchiveVerifier(manifest, test_restore=args.test_restore)
    result = verifier.verify(args.archive)

    if not args.quiet:
        _print_result(result)

    if args.json:
        ReportBuilder(result).write_json(args.json)
        print(f"JSON report: {args.json}")
    if args.html:
        ReportBuilder(result).write_html(args.html)
        print(f"HTML report: {args.html}")

    if not args.no_history:
        history = VerificationHistory(args.db)
        run_id = history.record(result)
        print(f"Logged to history as run #{run_id} ({args.db})")

    return 0 if result.ok else 1


def _cmd_history(args: argparse.Namespace) -> int:
    history = VerificationHistory(args.db)
    if args.stats:
        stats = history.stats()
        for k, v in stats.items():
            print(f"{k:24s} {v}")
        return 0
    rows = history.list_all(limit=args.limit)
    if not rows:
        print("No verification runs recorded yet.")
        return 0
    print(f"{'id':>4}  {'when (UTC)':<25}  {'ok':<3}  {'restore':<7}  archive")
    print("-" * 88)
    for r in rows:
        print(
            f"{r['id']:>4}  {r['finished_utc']:<25}  "
            f"{('yes' if r['ok'] else 'NO '):<3}  "
            f"{('yes' if r['restore_verified'] else '—'):<7}  "
            f"{r['archive_path']}"
        )
    return 0


def _print_result(result) -> None:
    print(f"Archive:   {result.archive_path}")
    print(f"SHA-256:   {result.archive_sha256}")
    print(f"Size:      {result.archive_size} bytes")
    print(f"Duration:  {result.started_utc} -> {result.finished_utc}")
    if result.restore_verified:
        print("Restore:   verified (extracted to temp dir)")
    print("Summary:  ", ", ".join(f"{k}={v}" for k, v in sorted(result.summary.items())) or "(empty)")
    for m in result.member_results:
        if m.status != "ok":
            print(f"  [{m.status}] {m.path}  {m.detail or ''}")
    if result.errors:
        print("Errors:")
        for e in result.errors:
            print(f"  - {e}")
    print("VERDICT:   " + ("PASS ✓" if result.ok else "FAIL ✗"))


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "build-manifest":
        return _cmd_build_manifest(args)
    if args.command == "verify":
        return _cmd_verify(args)
    if args.command == "history":
        return _cmd_history(args)
    parser.error("unknown command")  # pragma: no cover
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
