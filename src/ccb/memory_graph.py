# -*- coding: utf-8 -*-
"""
Local Semantic Memory Graph with Hybrid Embedding and BFS Cascading Retrieval.
Supports native fastembed BGE models with an intelligent pure-Python TF-IDF fallback
that works with ABSOLUTELY ZERO third-party libraries (including numpy).
"""
import os
import time
import json
import sqlite3
import math
from typing import List, Dict, Any, Tuple, Set

# Lazy import flag for fastembed and numpy
HAS_FASTEMBED = False
try:
    from fastembed import TextEmbedding
    import numpy as np
    HAS_FASTEMBED = True
except ImportError:
    pass


class SimpleTfidfVectorizer:
    """
    Highly robust, 100% pure-Python TF-IDF vectorizer fallback.
    No numpy or external dependencies required.
    """
    def __init__(self):
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.doc_count = 0

    def tokenize(self, text: str) -> List[str]:
        return [w.lower() for w in text.split() if w.isalnum() and len(w) > 1]

    def fit_and_vectorize(self, texts: List[str]) -> List[List[float]]:
        self.vocab = {}
        self.idf = {}
        self.doc_count = len(texts)
        
        doc_tfs = []
        df: Dict[str, int] = {}
        
        for text in texts:
            tokens = self.tokenize(text)
            tf: Dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            doc_tfs.append(tf)
            
            for t in set(tokens):
                df[t] = df.get(t, 0) + 1
                
        # Build vocabulary and IDF
        for t, f in df.items():
            self.vocab[t] = len(self.vocab)
            self.idf[t] = math.log((1 + self.doc_count) / (1 + f)) + 1.0
            
        vectors = []
        dim = len(self.vocab)
        if dim == 0:
            return [[0.0] for _ in texts]
            
        for tf in doc_tfs:
            vec = [0.0] * dim
            for t, count in tf.items():
                if t in self.vocab:
                    vec[self.vocab[t]] = count * self.idf[t]
            # Normalize vector to L2 unit length
            sq_sum = sum(x * x for x in vec)
            norm = math.sqrt(sq_sum)
            if norm > 0:
                vec = [x / norm for x in vec]
            vectors.append(vec)
        return vectors

    def vectorize(self, text: str) -> List[float]:
        dim = len(self.vocab)
        if dim == 0:
            return [0.0]
            
        tokens = self.tokenize(text)
        vec = [0.0] * dim
        tf: Dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
            
        for t, count in tf.items():
            if t in self.vocab:
                vec[self.vocab[t]] = count * self.idf[t]
                
        sq_sum = sum(x * x for x in vec)
        norm = math.sqrt(sq_sum)
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


class LocalEmbeddingService:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.use_onnx = HAS_FASTEMBED
        self.model = None
        self.fallback_vectorizer = SimpleTfidfVectorizer()
        self.corpus_docs: List[Tuple[str, str]] = []
        
        if self.use_onnx:
            try:
                self.model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
            except Exception:
                self.use_onnx = False

    def train_fallback(self, docs: List[Tuple[str, str]]):
        """Fit TF-IDF fallback vectorizer on existing node content corpus."""
        self.corpus_docs = docs
        if not self.use_onnx and docs:
            contents = [d[1] for d in docs]
            self.fallback_vectorizer.fit_and_vectorize(contents)

    def embed_text(self, text: str) -> List[float]:
        if self.use_onnx and self.model:
            try:
                embeddings = list(self.model.embed([text]))
                return list(embeddings[0])
            except Exception:
                pass
        
        # Fallback to TF-IDF vector
        return self.fallback_vectorizer.vectorize(text)


class MemoryGraphEngine:
    def __init__(self, db_path: str = None):
        if db_path is None:
            self.db_path = os.path.expanduser("~/.ccb/memory_graph.db")
        else:
            self.db_path = db_path
        self._init_db()
        self._refresh_embedding_fallback()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_nodes (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                vector BLOB NOT NULL,
                timestamp REAL NOT NULL
            );""")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                PRIMARY KEY (source_id, target_id, relation),
                FOREIGN KEY(source_id) REFERENCES memory_nodes(id) ON DELETE CASCADE,
                FOREIGN KEY(target_id) REFERENCES memory_nodes(id) ON DELETE CASCADE
            );""")

    def _refresh_embedding_fallback(self):
        """Pre-load all documents to fit TF-IDF vectorizer and re-calculate all vectors in db if neural is off."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT id, content FROM memory_nodes")
            docs = cursor.fetchall()
            
        service = LocalEmbeddingService.get_instance()
        service.train_fallback(docs)
        
        # Dynamically recalculate all stored TF-IDF vectors to match the newly fit vocabulary
        if not service.use_onnx and docs:
            import struct
            with sqlite3.connect(self.db_path) as conn:
                for node_id, content in docs:
                    vec = service.embed_text(content)
                    vector_bytes = struct.pack(f"{len(vec)}f", *vec)
                    conn.execute("UPDATE memory_nodes SET vector=? WHERE id=?", (vector_bytes, node_id))

    def insert_node(self, node_id: str, category: str, content: str):
        # Temporarily insert dummy vector to initialize node in db
        dummy_vector = [0.0]
        import struct
        vector_bytes = struct.pack(f"{len(dummy_vector)}f", *dummy_vector)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory_nodes VALUES (?, ?, ?, ?, ?)",
                (node_id, category, content, vector_bytes, time.time())
            )
        # Dynamically train and update all vectors in DB based on the new corpus
        self._refresh_embedding_fallback()

    def insert_edge(self, source_id: str, target_id: str, relation: str, weight: float = 1.0):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory_edges VALUES (?, ?, ?, ?)",
                (source_id, target_id, relation, weight)
            )

    @staticmethod
    def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
        if len(v1) != len(v2) or len(v1) == 0:
            return 0.0
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot_product / (norm1 * norm2)

    def search_semantic_subgraph(self, query: str, similarity_threshold: float = 0.55, limit: int = 5) -> Dict[str, Any]:
        """
        Retrieves the semantic subgraph using combined cosine vector similarity and 2-hop BFS relations.
        """
        # Ensure our vectorizer vocabulary is fit on the latest db state
        self._refresh_embedding_fallback()
        
        query_vec = LocalEmbeddingService.get_instance().embed_text(query)
        seeds = []

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT id, category, content, vector, timestamp FROM memory_nodes")
            for row in cursor.fetchall():
                node_id, cat, content, v_bytes, ts = row
                
                import struct
                num_floats = len(v_bytes) // 4
                if num_floats > 0:
                    v_list = list(struct.unpack(f"{num_floats}f", v_bytes))
                    
                    if len(v_list) == len(query_vec):
                        sim = self._cosine_similarity(query_vec, v_list)
                        if sim >= similarity_threshold:
                            seeds.append((sim, {"id": node_id, "category": cat, "content": content, "timestamp": ts}))

        # Sort and take top matches as seeds
        seeds = sorted(seeds, key=lambda x: x[0], reverse=True)[:limit]
        seed_ids = [s[1]["id"] for s in seeds]

        if not seed_ids:
            return {"subgraph_nodes": [], "warnings": []}

        # Perform 2-hop BFS
        visited = set(seed_ids)
        queue = list(seed_ids)
        related_nodes = {s[1]["id"]: s[1] for s in seeds}
        contradictions = []

        with sqlite3.connect(self.db_path) as conn:
            for depth in range(2):
                if not queue:
                    break
                next_queue = []
                placeholders = ",".join("?" for _ in queue)
                
                edges_cursor = conn.execute(
                    f"SELECT source_id, target_id, relation, weight FROM memory_edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                    queue + queue
                )
                
                for src, tgt, rel, wt in edges_cursor.fetchall():
                    neighbor = tgt if src in visited else src
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_queue.append(neighbor)
                        
                        node_row = conn.execute("SELECT category, content, timestamp FROM memory_nodes WHERE id=?", (neighbor,)).fetchone()
                        if node_row:
                            cat, content, ts = node_row
                            related_nodes[neighbor] = {
                                "id": neighbor, "category": cat, "content": content, "timestamp": ts,
                                "via_relation": rel, "via_weight": wt
                            }
                            
                            if rel == "Contradicts":
                                contradictions.append(f"Conflict warning: Node '{src}' and '{tgt}' contain contradicting updates.")
                queue = next_queue

        return {
            "subgraph_nodes": list(related_nodes.values()),
            "warnings": contradictions
        }
