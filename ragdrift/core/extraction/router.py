"""Route files to the appropriate extractor based on file extension."""

from pathlib import Path

from ragdrift.core.extraction.markdown import extract_markdown
from ragdrift.core.extraction.pdf import extract_pdf
from ragdrift.core.extraction.schema import ExtractionResult
from ragdrift.core.extraction.text import extract_text

_EXTENSION_MAP = {
    ".txt": extract_text,
    ".md": extract_markdown,
    ".pdf": extract_pdf,
}


def extract(path: Path) -> ExtractionResult:
    """Extract content from a file, routing to the correct extractor.

    Parameters
    ----------
    path:
        Path to the file to extract.

    Returns
    -------
    ExtractionResult
        Structured extraction output.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    ValueError
        If the file extension is not supported.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    extractor = _EXTENSION_MAP.get(suffix)

    if extractor is None:
        supported = ", ".join(sorted(_EXTENSION_MAP.keys()))
        raise ValueError(
            f"Unsupported file extension '{suffix}'. "
            f"Supported extensions: {supported}"
        )

    return extractor(path)
