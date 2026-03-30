"""BM25 sparse retrieval wrapper using rank-bm25."""
import re


class BM25Index:
    """Simple BM25 index for text retrieval."""

    def __init__(self):
        self._corpus_tokens: list[list[str]] = []
        self._doc_ids: list[str] = []
        self._chunks: list[str] = []
        self._chunk_to_doc: list[str] = []
        self._bm25 = None

    def add_document(self, doc_id: str, chunks: list[str]) -> None:
        """Add a document's chunks to the index."""
        for chunk in chunks:
            tokens = self._tokenize(chunk)
            self._corpus_tokens.append(tokens)
            self._chunks.append(chunk)
            self._chunk_to_doc.append(doc_id)
            self._doc_ids.append(doc_id)

    def build(self) -> None:
        """Build the BM25 index. Must call after adding all documents."""
        from rank_bm25 import BM25Okapi
        if self._corpus_tokens:
            self._bm25 = BM25Okapi(self._corpus_tokens)

    def query(self, query_text: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Query the index, return list of (doc_id, score) tuples."""
        if self._bm25 is None:
            return []
        tokens = self._tokenize(query_text)
        scores = self._bm25.get_scores(tokens)

        # Get top-k chunk indices
        indexed_scores = list(enumerate(scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)

        # Deduplicate by doc_id, keeping highest score
        seen_docs = {}
        for idx, score in indexed_scores:
            doc_id = self._chunk_to_doc[idx]
            if doc_id not in seen_docs:
                seen_docs[doc_id] = score
            if len(seen_docs) >= top_k:
                break

        return [(doc_id, score) for doc_id, score in seen_docs.items()]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple whitespace tokenizer with lowercasing."""
        return re.findall(r'\b\w+\b', text.lower())
