"""Core archive verification — integrity, manifest, and test-restore checks."""

from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .manifest import Manifest, ManifestEntry


@dataclass
class MemberResult:
    path: str
    observed_sha256: str
    observed_size: int
    status: str  # "ok", "checksum_mismatch", "missing", "unexpected", "size_mismatch"
    detail: Optional[str] = None


@dataclass
class VerificationResult:
    """Outcome of verifying one archive against a manifest."""

    archive_path: str
    archive_sha256: str
    archive_size: int
    started_utc: str
    finished_utc: str
    ok: bool
    member_results: List[MemberResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    restore_verified: bool = False

    # ---- Summary helpers -------------------------------------------------

    @property
    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for m in self.member_results:
            counts[m.status] = counts.get(m.status, 0) + 1
        return counts

    def failed_members(self) -> List[MemberResult]:
        return [m for m in self.member_results if m.status != "ok"]


class UnsupportedArchive(Exception):
    pass


class ArchiveVerifier:
    """Verify tar / tar.gz / tar.bz2 / zip archives against a manifest."""

    SUPPORTED_SUFFIXES = {
        ".tar",
        ".tar.gz",
        ".tgz",
        ".tar.bz2",
        ".tbz2",
        ".tar.xz",
        ".txz",
        ".zip",
    }

    def __init__(self, manifest: Manifest, test_restore: bool = False):
        self.manifest = manifest
        self.test_restore = test_restore

    # ---- Public API ------------------------------------------------------

    def verify(self, archive_path: Path | str) -> VerificationResult:
        archive_path = Path(archive_path)
        if not archive_path.exists():
            raise FileNotFoundError(archive_path)

        started = _utcnow()
        archive_sha, archive_size = _hash_file(archive_path)

        members: List[MemberResult] = []
        errors: List[str] = []
        restore_ok = False

        try:
            if _is_tarfile(archive_path):
                members = list(self._verify_tar(archive_path))
            elif archive_path.suffix.lower() == ".zip":
                members = list(self._verify_zip(archive_path))
            else:
                raise UnsupportedArchive(
                    f"Unsupported archive extension: {archive_path.suffix}"
                )

            if self.test_restore:
                restore_ok = self._test_restore(archive_path)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{type(exc).__name__}: {exc}")

        ok = not errors and all(m.status == "ok" for m in members)
        if self.test_restore and not restore_ok:
            ok = False

        return VerificationResult(
            archive_path=str(archive_path),
            archive_sha256=archive_sha,
            archive_size=archive_size,
            started_utc=started,
            finished_utc=_utcnow(),
            ok=ok,
            member_results=members,
            errors=errors,
            restore_verified=restore_ok,
        )

    # ---- Tar -------------------------------------------------------------

    def _verify_tar(self, archive_path: Path) -> Iterable[MemberResult]:
        manifest_map = self.manifest.as_map()
        seen: set[str] = set()

        with tarfile.open(archive_path, "r:*") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                path = member.name.lstrip("./")
                seen.add(path)
                fobj = tf.extractfile(member)
                if fobj is None:
                    yield MemberResult(
                        path=path,
                        observed_sha256="",
                        observed_size=0,
                        status="missing",
                        detail="tar member could not be opened",
                    )
                    continue
                digest, size = _hash_stream(fobj)
                yield _compare(path, digest, size, manifest_map)

        yield from _report_missing(manifest_map, seen)

    # ---- Zip -------------------------------------------------------------

    def _verify_zip(self, archive_path: Path) -> Iterable[MemberResult]:
        manifest_map = self.manifest.as_map()
        seen: set[str] = set()

        with zipfile.ZipFile(archive_path, "r") as zf:
            # zip has a built-in CRC check — run it first
            bad = zf.testzip()
            if bad is not None:
                yield MemberResult(
                    path=bad,
                    observed_sha256="",
                    observed_size=0,
                    status="checksum_mismatch",
                    detail="zipfile testzip() reported CRC failure",
                )

            for info in zf.infolist():
                if info.is_dir():
                    continue
                path = info.filename
                seen.add(path)
                with zf.open(info, "r") as fobj:
                    digest, size = _hash_stream(fobj)
                yield _compare(path, digest, size, manifest_map)

        yield from _report_missing(manifest_map, seen)

    # ---- Restore test ----------------------------------------------------

    def _test_restore(self, archive_path: Path) -> bool:
        """Extract to a temp directory to prove the archive is usable."""
        with tempfile.TemporaryDirectory(prefix="biv-restore-") as tmp:
            tmp_path = Path(tmp)
            try:
                if _is_tarfile(archive_path):
                    with tarfile.open(archive_path, "r:*") as tf:
                        _safe_extract_tar(tf, tmp_path)
                else:
                    with zipfile.ZipFile(archive_path, "r") as zf:
                        _safe_extract_zip(zf, tmp_path)
            except Exception:
                return False
        return True


# ---- Helpers -------------------------------------------------------------


def _compare(
    path: str,
    digest: str,
    size: int,
    manifest_map: Dict[str, ManifestEntry],
) -> MemberResult:
    entry = manifest_map.get(path)
    if entry is None:
        return MemberResult(path, digest, size, "unexpected")
    if entry.size != size:
        return MemberResult(
            path, digest, size, "size_mismatch",
            detail=f"expected {entry.size} bytes, got {size}",
        )
    if entry.sha256 != digest:
        return MemberResult(
            path, digest, size, "checksum_mismatch",
            detail=f"expected {entry.sha256[:12]}…, got {digest[:12]}…",
        )
    return MemberResult(path, digest, size, "ok")


def _report_missing(
    manifest_map: Dict[str, ManifestEntry], seen: set[str]
) -> Iterable[MemberResult]:
    for path, entry in manifest_map.items():
        if path in seen:
            continue
        if not entry.required:
            continue
        yield MemberResult(
            path=path,
            observed_sha256="",
            observed_size=0,
            status="missing",
            detail="declared in manifest but absent from archive",
        )


def _hash_file(path: Path, chunk: int = 1 << 20) -> Tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
            size += len(b)
    return h.hexdigest(), size


def _hash_stream(fobj, chunk: int = 1 << 20) -> Tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    while True:
        b = fobj.read(chunk)
        if not b:
            break
        if isinstance(b, str):  # pragma: no cover - defensive
            b = b.encode("utf-8")
        h.update(b)
        size += len(b)
    return h.hexdigest(), size


def _is_tarfile(path: Path) -> bool:
    try:
        return tarfile.is_tarfile(path)
    except Exception:
        return False


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


# ---- Safe extraction (path-traversal guard) ------------------------------


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for m in tf.getmembers():
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest)):
            raise RuntimeError(f"Blocked path traversal in tar: {m.name}")
    tf.extractall(dest)


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for name in zf.namelist():
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest)):
            raise RuntimeError(f"Blocked path traversal in zip: {name}")
    zf.extractall(dest)
