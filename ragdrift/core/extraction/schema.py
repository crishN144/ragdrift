from typing import TypedDict


class ExtractionResult(TypedDict):
    doc_id: str
    source_path: str
    content: str
    headings: list[str]  # e.g. ["# Title", "## Section", "### Sub"]
    tables: list[str]  # raw table text if found
    file_hash: str  # MD5 of source file
    parser_type: str  # "text" | "markdown" | "pdf"
    extractor_version: str
