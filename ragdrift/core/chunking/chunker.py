"""Recursive text splitter for chunking documents."""

from __future__ import annotations


class RecursiveChunker:
    """Split text into overlapping chunks using a recursive strategy.

    The splitter tries progressively finer separators:

    1. Paragraph breaks (``\\n\\n``)
    2. Sentence boundaries (``. `` followed by an uppercase letter)
    3. Hard character-limit splits (respecting word boundaries when possible)

    Parameters
    ----------
    chunk_size:
        Target maximum number of characters per chunk.
    chunk_overlap:
        Number of overlapping characters between consecutive chunks.
    """

    _SEPARATORS = ["\n\n", ". ", " "]

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be non-negative")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk(self, text: str) -> list[str]:
        """Split *text* into a list of chunks.

        Parameters
        ----------
        text:
            The input text to split.

        Returns
        -------
        list[str]
            Ordered list of text chunks.
        """
        if not text:
            return []

        return self._split_recursive(text, separator_index=0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_recursive(self, text: str, separator_index: int) -> list[str]:
        """Recursively split text using separators of increasing granularity."""
        # Base case: text already fits in a single chunk.
        if len(text) <= self.chunk_size:
            stripped = text.strip()
            return [stripped] if stripped else []

        # If we've exhausted all separators, hard-split by character limit.
        if separator_index >= len(self._SEPARATORS):
            return self._hard_split(text)

        separator = self._SEPARATORS[separator_index]
        segments = text.split(separator)

        # If splitting didn't help (only one segment), try the next separator.
        if len(segments) <= 1:
            return self._split_recursive(text, separator_index + 1)

        chunks: list[str] = []
        current = ""

        for segment in segments:
            candidate = (
                current + separator + segment if current else segment
            )

            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                # Flush the current accumulator.
                if current:
                    flushed = self._finalize(current, separator_index)
                    chunks.extend(flushed)

                    # Build overlap from the tail of the flushed text.
                    overlap_text = self._get_overlap(current)
                    current = overlap_text + separator + segment if overlap_text else segment
                else:
                    # Single segment exceeds chunk_size; recurse deeper.
                    flushed = self._split_recursive(segment, separator_index + 1)
                    chunks.extend(flushed)
                    current = ""

        # Flush remaining text.
        if current:
            flushed = self._finalize(current, separator_index)
            chunks.extend(flushed)

        return chunks

    def _finalize(self, text: str, separator_index: int) -> list[str]:
        """Finalize a segment: if it fits, return it; otherwise recurse."""
        stripped = text.strip()
        if not stripped:
            return []
        if len(stripped) <= self.chunk_size:
            return [stripped]
        return self._split_recursive(stripped, separator_index + 1)

    def _hard_split(self, text: str) -> list[str]:
        """Split text into chunks at the character level.

        Tries to break on the last space within the chunk_size window to
        avoid splitting words.
        """
        chunks: list[str] = []
        start = 0

        while start < len(text):
            end = start + self.chunk_size

            if end >= len(text):
                remainder = text[start:].strip()
                if remainder:
                    chunks.append(remainder)
                break

            # Try to find a word boundary.
            slice_text = text[start:end]
            last_space = slice_text.rfind(" ")
            if last_space > 0:
                end = start + last_space

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(chunk_text)

            # Advance with overlap.
            start = end - self.chunk_overlap

        return chunks

    def _get_overlap(self, text: str) -> str:
        """Return the trailing *chunk_overlap* characters of *text*."""
        if self.chunk_overlap <= 0:
            return ""
        return text[-self.chunk_overlap :]
