"""Plain text extractor for .txt files."""

import hashlib
import re
import uuid
from pathlib import Path

from ragdrift.core.extraction.schema import ExtractionResult

_EXTRACTOR_VERSION = "1.0.0"

_NUMBERED_HEADING_RE = re.compile(r"^\d+[\.\)]\s+\S")


def _is_heading(line: str) -> bool:
    """Detect headings in plain text.

    A line is considered a heading if it is:
    - All uppercase (at least 3 alphabetic characters), or
    - Starts with a number followed by a period/paren and text.
    """
    stripped = line.strip()
    if not stripped:
        return False

    alpha_chars = [c for c in stripped if c.isalpha()]
    if len(alpha_chars) >= 3 and stripped == stripped.upper():
        return True

    if _NUMBERED_HEADING_RE.match(stripped):
        return True

    return False


def _compute_md5(path: Path) -> str:
    """Compute the MD5 hex digest of a file."""
    hasher = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_text(path: Path) -> ExtractionResult:
    """Extract content from a plain text file.

    Parameters
    ----------
    path:
        Path to a ``.txt`` file.

    Returns
    -------
    ExtractionResult
        Structured extraction output.
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8")

    headings: list[str] = []
    for line in content.splitlines():
        if _is_heading(line):
            headings.append(line.strip())

    return ExtractionResult(
        doc_id=uuid.uuid4().hex,
        source_path=str(path.resolve()),
        content=content,
        headings=headings,
        tables=[],
        file_hash=_compute_md5(path),
        parser_type="text",
        extractor_version=_EXTRACTOR_VERSION,
    )
