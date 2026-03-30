"""PDF extractor using PyMuPDF (fitz)."""

import hashlib
import re
import uuid
from pathlib import Path

from ragdrift.core.extraction.schema import ExtractionResult

_EXTRACTOR_VERSION = "1.0.0"

_NUMBERED_HEADING_RE = re.compile(r"^\d+[\.\)]\s+\S")


def _compute_md5(path: Path) -> str:
    """Compute the MD5 hex digest of a file."""
    hasher = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _detect_headings_by_font(page) -> list[str]:  # type: ignore[no-untyped-def]
    """Detect headings using font size information from a PyMuPDF page.

    Spans with a font size significantly above the median are treated as
    headings.  Falls back to an empty list when font information is
    unavailable.
    """
    try:
        blocks = page.get_text("dict", flags=0)["blocks"]
    except Exception:
        return []

    spans: list[tuple[float, str]] = []
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                size = span.get("size", 0.0)
                if text:
                    spans.append((size, text))

    if not spans:
        return []

    sizes = sorted({s for s, _ in spans})
    if len(sizes) < 2:
        return []

    # Consider anything above the 75th-percentile size a heading.
    threshold = sizes[len(sizes) * 3 // 4]
    return [text for size, text in spans if size > threshold]


def _detect_headings_by_caps(text: str) -> list[str]:
    """Fallback heading detection using ALL CAPS or numbered lines."""
    headings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        alpha = [c for c in stripped if c.isalpha()]
        if len(alpha) >= 3 and stripped == stripped.upper():
            headings.append(stripped)
        elif _NUMBERED_HEADING_RE.match(stripped):
            headings.append(stripped)
    return headings


def extract_pdf(path: Path) -> ExtractionResult:
    """Extract content from a PDF file using PyMuPDF.

    Parameters
    ----------
    path:
        Path to a ``.pdf`` file.

    Returns
    -------
    ExtractionResult
        Structured extraction output.

    Raises
    ------
    ImportError
        If PyMuPDF is not installed.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ImportError(
            "PyMuPDF is required for PDF extraction. "
            "Install it with: pip install PyMuPDF"
        ) from exc

    path = Path(path)
    doc = fitz.open(str(path))

    pages_text: list[str] = []
    all_headings: list[str] = []

    for page in doc:
        page_text = page.get_text("text")
        pages_text.append(page_text)

        font_headings = _detect_headings_by_font(page)
        if font_headings:
            all_headings.extend(font_headings)

    doc.close()

    content = "\n".join(pages_text)

    # If font-based detection yielded nothing, fall back to ALL CAPS.
    if not all_headings:
        all_headings = _detect_headings_by_caps(content)

    return ExtractionResult(
        doc_id=uuid.uuid4().hex,
        source_path=str(path.resolve()),
        content=content,
        headings=all_headings,
        tables=[],
        file_hash=_compute_md5(path),
        parser_type="pdf",
        extractor_version=_EXTRACTOR_VERSION,
    )
