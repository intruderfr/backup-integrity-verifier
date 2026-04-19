"""backup-integrity-verifier — validate, track, and report on backup archives."""

__version__ = "0.1.0"
__author__ = "Aslam Ahamed"

from .verifier import ArchiveVerifier, VerificationResult
from .manifest import Manifest, ManifestEntry
from .storage import VerificationHistory
from .report import ReportBuilder

__all__ = [
    "ArchiveVerifier",
    "VerificationResult",
    "Manifest",
    "ManifestEntry",
    "VerificationHistory",
    "ReportBuilder",
]
