"""Qdrant vector index wrapper with sentence-transformers.

Only available when ragdrift[vector] is installed.
"""

class VectorIndex:
    """Vector index using Qdrant and sentence-transformers."""

    def __init__(self, collection_name: str = "ragdrift", url: str = "http://localhost:6333"):
        self._collection_name = collection_name
        self._url = url
        self._model = None
        self._client = None
        self._doc_ids: list[str] = []
        self._chunks: list[str] = []
        self._chunk_to_doc: list[str] = []

    def _ensure_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer("all-MiniLM-L6-v2")
            except ImportError:
                raise ImportError(
                    "sentence-transformers required. Install with: pip install ragdrift[vector]"
                )

    def _ensure_client(self):
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
                from qdrant_client.models import Distance, VectorParams
                self._client = QdrantClient(url=self._url)
                # Create collection if not exists
                collections = [c.name for c in self._client.get_collections().collections]
                if self._collection_name not in collections:
                    self._client.create_collection(
                        collection_name=self._collection_name,
                        vectors_config=VectorParams(size=384, distance=Distance.COSINE),
                    )
            except ImportError:
                raise ImportError(
                    "qdrant-client required. Install with: pip install ragdrift[vector]"
                )

    def add_document(self, doc_id: str, chunks: list[str]) -> None:
        """Add a document's chunks to the index."""
        for chunk in chunks:
            self._chunks.append(chunk)
            self._chunk_to_doc.append(doc_id)

    def build(self) -> None:
        """Build the vector index by encoding chunks and uploading to Qdrant."""
        self._ensure_model()
        self._ensure_client()
        from qdrant_client.models import PointStruct

        if not self._chunks:
            return

        embeddings = self._model.encode(self._chunks)
        points = [
            PointStruct(
                id=i,
                vector=embedding.tolist(),
                payload={"doc_id": self._chunk_to_doc[i], "chunk_text": self._chunks[i]},
            )
            for i, embedding in enumerate(embeddings)
        ]
        self._client.upsert(collection_name=self._collection_name, points=points)

    def query(self, query_text: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Query the vector index."""
        self._ensure_model()
        self._ensure_client()

        query_vector = self._model.encode(query_text).tolist()
        results = self._client.search(
            collection_name=self._collection_name,
            query_vector=query_vector,
            limit=top_k * 2,  # Get extra to deduplicate
        )

        seen_docs = {}
        for result in results:
            doc_id = result.payload["doc_id"]
            if doc_id not in seen_docs:
                seen_docs[doc_id] = result.score
            if len(seen_docs) >= top_k:
                break

        return [(doc_id, score) for doc_id, score in seen_docs.items()]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Encode texts to embeddings. Useful for semantic diff."""
        self._ensure_model()
        return self._model.encode(texts).tolist()
