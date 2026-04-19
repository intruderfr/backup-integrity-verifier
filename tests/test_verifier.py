"""End-to-end tests for backup-integrity-verifier."""

from __future__ import annotations

import json
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backup_verifier.manifest import Manifest
from backup_verifier.report import ReportBuilder
from backup_verifier.storage import VerificationHistory
from backup_verifier.verifier import ArchiveVerifier


# ---- Fixtures ------------------------------------------------------------


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "nested").mkdir(parents=True)
    (src / "hello.txt").write_text("hello world\n")
    (src / "data.bin").write_bytes(b"\x00\x01\x02" * 128)
    (src / "nested" / "readme.md").write_text("# nested\n")
    return src


@pytest.fixture
def manifest(source_tree: Path) -> Manifest:
    return Manifest.build_from_directory(
        source_tree,
        archive_name="test",
        created_utc=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    )


def _make_tar(src: Path, dest: Path, mode: str = "w:gz") -> Path:
    with tarfile.open(dest, mode) as tf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                tf.add(p, arcname=p.relative_to(src).as_posix())
    return dest


def _make_zip(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(src).as_posix())
    return dest


# ---- Happy-path tests ----------------------------------------------------


def test_tar_gz_passes(tmp_path: Path, source_tree: Path, manifest: Manifest):
    archive = _make_tar(source_tree, tmp_path / "ok.tar.gz", "w:gz")
    result = ArchiveVerifier(manifest).verify(archive)
    assert result.ok
    assert result.summary.get("ok") == len(manifest.entries)
    assert not result.failed_members()


def test_zip_passes(tmp_path: Path, source_tree: Path, manifest: Manifest):
    archive = _make_zip(source_tree, tmp_path / "ok.zip")
    result = ArchiveVerifier(manifest).verify(archive)
    assert result.ok


def test_test_restore_extracts_successfully(
    tmp_path: Path, source_tree: Path, manifest: Manifest
):
    archive = _make_tar(source_tree, tmp_path / "restore.tar.gz", "w:gz")
    result = ArchiveVerifier(manifest, test_restore=True).verify(archive)
    assert result.ok
    assert result.restore_verified is True


# ---- Failure-path tests --------------------------------------------------


def test_missing_file_is_flagged(tmp_path: Path, source_tree: Path, manifest: Manifest):
    # Remove a file, re-archive — manifest still expects it.
    (source_tree / "hello.txt").unlink()
    archive = _make_tar(source_tree, tmp_path / "bad.tar.gz", "w:gz")
    result = ArchiveVerifier(manifest).verify(archive)
    assert not result.ok
    missing = [m for m in result.member_results if m.status == "missing"]
    assert any(m.path == "hello.txt" for m in missing)


def test_tampered_file_is_flagged(
    tmp_path: Path, source_tree: Path, manifest: Manifest
):
    (source_tree / "hello.txt").write_text("TAMPERED!\n")
    archive = _make_zip(source_tree, tmp_path / "tampered.zip")
    result = ArchiveVerifier(manifest).verify(archive)
    assert not result.ok
    bad = [m for m in result.member_results if m.status != "ok"]
    assert any(m.path == "hello.txt" and m.status in {"checksum_mismatch", "size_mismatch"} for m in bad)


def test_unexpected_file_is_flagged(
    tmp_path: Path, source_tree: Path, manifest: Manifest
):
    (source_tree / "surprise.txt").write_text("boo!\n")
    archive = _make_tar(source_tree, tmp_path / "extra.tar.gz", "w:gz")
    result = ArchiveVerifier(manifest).verify(archive)
    assert not result.ok
    assert any(m.path == "surprise.txt" and m.status == "unexpected"
               for m in result.member_results)


# ---- Manifest IO ---------------------------------------------------------


def test_manifest_roundtrip(tmp_path: Path, manifest: Manifest):
    p = tmp_path / "m.json"
    manifest.to_json(p)
    loaded = Manifest.from_json(p)
    assert loaded.archive_name == manifest.archive_name
    assert len(loaded.entries) == len(manifest.entries)
    assert loaded.entries[0].sha256 == manifest.entries[0].sha256


# ---- Reports & storage ---------------------------------------------------


def test_html_and_json_reports(tmp_path: Path, source_tree: Path, manifest: Manifest):
    archive = _make_tar(source_tree, tmp_path / "r.tar.gz", "w:gz")
    result = ArchiveVerifier(manifest).verify(archive)
    builder = ReportBuilder(result)
    j = builder.to_json()
    payload = json.loads(j)
    assert payload["ok"] is True
    assert payload["archive_sha256"] == result.archive_sha256
    html = builder.to_html()
    assert "Backup Verification Report" in html
    assert "PASS" in html


def test_history_records_and_reads(tmp_path: Path, source_tree: Path, manifest: Manifest):
    archive = _make_tar(source_tree, tmp_path / "h.tar.gz", "w:gz")
    result = ArchiveVerifier(manifest).verify(archive)
    db = VerificationHistory(tmp_path / "hist.sqlite3")
    run_id = db.record(result)
    assert run_id >= 1
    latest = db.latest_for(result.archive_path)
    assert latest is not None
    assert latest["ok"] == 1
    stats = db.stats()
    assert stats["total_runs"] == 1
    assert stats["passed"] == 1
