"""Markdown extractor for .md files."""

import hashlib
import re
import uuid
from pathlib import Path

from ragdrift.core.extraction.schema import ExtractionResult

_EXTRACTOR_VERSION = "1.0.0"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|$")


def _compute_md5(path: Path) -> str:
    """Compute the MD5 hex digest of a file."""
    hasher = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _extract_headings(content: str) -> list[str]:
    """Return all Markdown headings (lines starting with #)."""
    headings: list[str] = []
    for line in content.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            headings.append(line.strip())
    return headings


def _extract_tables(content: str) -> list[str]:
    """Extract contiguous blocks of Markdown table lines.

    A table block is a sequence of consecutive lines that contain ``|``.
    We require at least one separator row (``|---|---|``) to confirm it is
    actually a table rather than inline pipe usage.
    """
    tables: list[str] = []
    current_block: list[str] = []
    has_separator = False

    for line in content.splitlines():
        stripped = line.strip()
        if "|" in stripped:
            current_block.append(line)
            if _TABLE_SEPARATOR_RE.match(stripped):
                has_separator = True
        else:
            if current_block and has_separator:
                tables.append("\n".join(current_block))
            current_block = []
            has_separator = False

    # Flush trailing block
    if current_block and has_separator:
        tables.append("\n".join(current_block))

    return tables


def extract_markdown(path: Path) -> ExtractionResult:
    """Extract content from a Markdown file.

    Parameters
    ----------
    path:
        Path to a ``.md`` file.

    Returns
    -------
    ExtractionResult
        Structured extraction output.
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8")

    return ExtractionResult(
        doc_id=uuid.uuid4().hex,
        source_path=str(path.resolve()),
        content=content,
        headings=_extract_headings(content),
        tables=_extract_tables(content),
        file_hash=_compute_md5(path),
        parser_type="markdown",
        extractor_version=_EXTRACTOR_VERSION,
    )
