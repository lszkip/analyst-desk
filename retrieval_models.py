"""Pinned local ONNX models. Only prepare_models() is allowed to download.

Query/index code verifies model bytes and runs on CPU without network access.
Model revisions and individual file SHA-256 values live in retrieval_config.json.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parent
DEFAULT_MODELS = ROOT / "data" / "models"


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def config():
    value = json.loads((ROOT / "retrieval_config.json").read_text())
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported retrieval configuration.")
    bounds = {"window_tokens": (64, 320), "window_overlap": (0, 64),
              "prefix_tokens": (1, 90), "rrf_k": (1, 200),
              "candidate_passages": (5, 50), "candidate_windows": (5, 300),
              "max_branches": (1, 18)}
    for key, (low, high) in bounds.items():
        number = value.get(key)
        if type(number) is not int or not low <= number <= high:
            raise ValueError(f"Invalid bounded retrieval setting: {key}.")
    if (value["window_overlap"] >= value["window_tokens"] or
            value["candidate_windows"] < value["candidate_passages"] or value.get("dimension") != 384):
        raise ValueError("Inconsistent retrieval model/window configuration.")
    return value


def fingerprint():
    return hashlib.sha256(json.dumps(config(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_models(folder=DEFAULT_MODELS):
    for role, model in config()["models"].items():
        for name, expected in model["files"].items():
            path = Path(folder) / role / name
            if not path.is_file() or sha_file(path) != expected:
                raise ValueError(f"Missing or changed {role} model file {name}. Run prepare_retrieval_models.py.")
    return fingerprint()


def prepare_models(folder=DEFAULT_MODELS):
    from huggingface_hub import snapshot_download
    for role, model in config()["models"].items():
        directory = Path(folder) / role
        if all((directory / name).is_file() and sha_file(directory / name) == sha
               for name, sha in model["files"].items()):
            print(f"{role}: cached and verified", flush=True)
            continue
        snapshot_download(repo_id=model["repository"], revision=model["revision"],
                          allow_patterns=list(model["files"]), local_dir=directory,
                          force_download=any((directory / name).exists() for name in model["files"]))
        print(f"{role}: downloaded", flush=True)
    verify_models(folder)


class LocalModels:
    """A genuine embedding model and cross-encoder; never a hash-vector fallback."""
    def __init__(self, folder=DEFAULT_MODELS, threads=2):
        from fastembed import TextEmbedding
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        if type(threads) is not int or not 1 <= threads <= 8:
            raise ValueError("Use 1–8 model threads.")
        self.fingerprint = verify_models(folder)
        cfg = config()
        self.settings = cfg
        self.dimension = cfg["dimension"]
        shared = dict(local_files_only=True, cache_dir="/tmp/analyst-fastembed",
                      threads=threads, providers=["CPUExecutionProvider"])
        self.encoder = TextEmbedding(cfg["models"]["embedding"]["name"],
                                    specific_model_path=str(Path(folder) / "embedding"), **shared)
        self.reranker = TextCrossEncoder(cfg["models"]["reranker"]["name"],
                                    specific_model_path=str(Path(folder) / "reranker"), **shared)
        self.tokenizer = Tokenizer.from_file(str(Path(folder) / "embedding" / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.rerank_tokenizer = Tokenizer.from_file(str(Path(folder) / "reranker" / "tokenizer.json"))
        self.rerank_tokenizer.no_truncation()
        self.rerank_tokenizer.no_padding()

    def windows(self, text, size=None, overlap=None):
        """Character slices covering every token, including long table rows."""
        size = self.settings["window_tokens"] if size is None else size
        overlap = self.settings["window_overlap"] if overlap is None else overlap
        if type(size) is not int or type(overlap) is not int or not 1 <= size <= 320 or not 0 <= overlap < size:
            raise ValueError("Invalid bounded search-window token settings.")
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        offsets = tokens.offsets
        if not offsets:
            return []
        pieces = []
        for a in range(0, len(offsets), size - overlap):
            b = min(a + size, len(offsets))
            start = 0 if a == 0 else offsets[a][0]
            end = len(text) if b == len(offsets) else offsets[b - 1][1]
            pieces.append((text[start:end], start, end))
            if b == len(offsets):
                break
        return pieces

    def check_query(self, query):
        if len(self.tokenizer.encode(query).ids) > 96 or len(self.rerank_tokenizer.encode(query).ids) > 96:
            raise ValueError("Query exceeds 96 model tokens; split it into shorter research questions.")

    def embed(self, texts, *, query=False):
        texts = list(texts)
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        if any(len(self.tokenizer.encode(t).ids) > 450 for t in texts):
            raise ValueError("Search representation exceeds the embedding token limit.")
        values = np.asarray(list(self.encoder.embed(texts, batch_size=16)), dtype=np.float32)
        if values.shape != (len(texts), self.dimension) or not np.isfinite(values).all():
            raise ValueError("Embedding model returned invalid vectors.")
        norm = np.linalg.norm(values, axis=1, keepdims=True)
        if np.any(norm <= 0):
            raise ValueError("Embedding model returned a zero vector.")
        return values / norm

    def rerank(self, query, texts):
        self.check_query(query)
        if any(len(self.rerank_tokenizer.encode(query, text).ids) > 512 for text in texts):
            raise ValueError("Reranker input exceeds 512 tokens; no silent truncation is allowed.")
        scores = [float(x) for x in self.reranker.rerank(query, texts, batch_size=8)]
        if len(scores) != len(texts) or not np.isfinite(scores).all():
            raise ValueError("Reranker returned invalid scores.")
        return scores
