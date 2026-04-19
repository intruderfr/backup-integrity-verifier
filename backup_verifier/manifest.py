"""Manifest parsing — describes expected archive contents and checksums."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ManifestEntry:
    """One expected member inside a backup archive."""

    path: str
    sha256: str
    size: int
    required: bool = True

    def matches(self, observed_sha256: str, observed_size: int) -> bool:
        return self.sha256 == observed_sha256 and self.size == observed_size


@dataclass
class Manifest:
    """A declarative description of a backup's expected state."""

    archive_name: str
    created_utc: str
    entries: List[ManifestEntry] = field(default_factory=list)
    notes: Optional[str] = None

    # ---- IO ---------------------------------------------------------------

    @classmethod
    def from_json(cls, path: Path | str) -> "Manifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = [ManifestEntry(**e) for e in data.get("entries", [])]
        return cls(
            archive_name=data["archive_name"],
            created_utc=data["created_utc"],
            entries=entries,
            notes=data.get("notes"),
        )

    def to_json(self, path: Path | str) -> None:
        payload = {
            "archive_name": self.archive_name,
            "created_utc": self.created_utc,
            "notes": self.notes,
            "entries": [asdict(e) for e in self.entries],
        }
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ---- Build from a directory ------------------------------------------

    @classmethod
    def build_from_directory(
        cls,
        root: Path | str,
        archive_name: str,
        created_utc: str,
        required: bool = True,
    ) -> "Manifest":
        """Walk a directory and compute a manifest for every file inside."""
        root = Path(root).resolve()
        entries: List[ManifestEntry] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            digest, size = _hash_file(p)
            entries.append(
                ManifestEntry(path=rel, sha256=digest, size=size, required=required)
            )
        return cls(archive_name=archive_name, created_utc=created_utc, entries=entries)

    # ---- Lookup ----------------------------------------------------------

    def as_map(self) -> Dict[str, ManifestEntry]:
        return {e.path: e for e in self.entries}


def _hash_file(path: Path, chunk: int = 1 << 20) -> tuple[str, int]:
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
