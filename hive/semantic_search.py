"""Optional semantic/vector search for RustBrain.

Requires ``sentence-transformers``. Falls back to tag-based linear scan
when embeddings are unavailable.

Usage::

    from hive.semantic_search import SemanticIndex
    from hive.rust_brain import RustBrain

    brain = RustBrain()
    index = SemanticIndex(brain, model="all-MiniLM-L6-v2")
    index.index_all()

    results = index.search("authentication bug", top_k=5)
"""

from __future__ import annotations

import logging
from typing import Any

from hive.rust_brain import MemoryNode, RustBrain

_log = logging.getLogger("hive.semantic_search")


try:
    from sentence_transformers import SentenceTransformer

    _HAS_ST = True
except Exception:  # pragma: no cover
    _HAS_ST = False


try:
    import numpy as np

    _HAS_NUMPY = True
except Exception:  # pragma: no cover
    _HAS_NUMPY = False


class SemanticIndex:
    """In-memory semantic index over a RustBrain store.

    Parameters
    ----------
    brain:
        The RustBrain to index.
    model:
        Sentence-transformers model name.
    """

    def __init__(
        self,
        brain: RustBrain,
        *,
        model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self._brain = brain
        self._model_name = model
        self._model: Any = None
        self._vectors: dict[str, Any] = {}
        if _HAS_ST:
            try:
                self._model = SentenceTransformer(model)
                _log.info("Loaded embedding model: %s", model)
            except Exception as exc:
                _log.warning("Failed to load %s: %s", model, exc)

    def embed(self, text: str) -> Any:
        """Return the embedding vector for a text string."""
        if self._model is None:
            raise RuntimeError("sentence-transformers not available")
        return self._model.encode(text)

    def index_all(self) -> int:
        """Build embeddings for all nodes in the brain."""
        if self._model is None:
            _log.warning("No embedding model; skipping semantic index")
            return 0
        count = 0
        for node in self._brain.search():
            key = f"{node.key}:{node.node_id}"
            text = f"{node.key} {node.value} {' '.join(node.tags)}"
            self._vectors[key] = self.embed(text)
            count += 1
        return count

    def search(self, query: str, *, top_k: int = 5, min_score: float = 0.5) -> list[tuple[MemoryNode, float]]:
        """Semantic search over all nodes.

        Returns list of (node, cosine_similarity) sorted by score.
        """
        if not _HAS_ST or not _HAS_NUMPY or self._model is None:
            _log.warning("Semantic search unavailable; falling back to tag scan")
            return [(n, 1.0) for n in self._brain.search(tag=query)[:top_k]]

        if not self._vectors:
            self.index_all()

        q_vec = self.embed(query)
        scores: list[tuple[str, float]] = []
        for key, vec in self._vectors.items():
            # Cosine similarity
            score = float(np.dot(q_vec, vec) / (np.linalg.norm(q_vec) * np.linalg.norm(vec)))
            if score >= min_score:
                scores.append((key, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        results: list[tuple[MemoryNode, float]] = []
        for key, score in scores[:top_k]:
            # key format: "storage_key:node_id"
            storage_key = key.rsplit(":", 1)[0]
            node = self._brain.get(storage_key)
            if node:
                results.append((node, round(score, 4)))
        return results

    def add(self, node: MemoryNode) -> None:
        """Index a single node (call after remember)."""
        if self._model is None:
            return
        key = f"{node.key}:{node.node_id}"
        text = f"{node.key} {node.value} {' '.join(node.tags)}"
        self._vectors[key] = self.embed(text)


__all__ = ["SemanticIndex"]
