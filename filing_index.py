"""Resumable Qdrant indexing with an atomically published local generation.

No collection is deleted. Failed builds keep the last published index manifest.
Numeric vectors are cached without pickle and checked before reuse.
"""
from io import BytesIO
import json
import os
from pathlib import Path
import uuid

import numpy as np
from qdrant_client import QdrantClient, models as qm

from company_registry import DATA_DIR
from download_company_facts import _atomic_write
from filing_corpus import _json_bytes, digest
from retrieval_data import PassageCatalogue, RetrievalError, POLICY, bounded_json, search_units

DEFAULT_INDEX = DATA_DIR.parent / "retrieval"
BATCH = 64


def connect(url="http://127.0.0.1:6333", *, read_only=False):
    key = os.getenv("QDRANT_READ_ONLY_API_KEY" if read_only else "QDRANT_API_KEY")
    if not key:
        raise RetrievalError("Qdrant key is missing. Run setup_retrieval.py and load your local .env.")
    return QdrantClient(url=url, api_key=key, timeout=30, trust_env=False)


def point_id(unit_id):
    return str(uuid.UUID(unit_id[2:34]))


def payload(unit):
    return {k: unit[k] for k in ("unit_id", "passage_id", "ticker", "fiscal_year", "item", "kind", "source_sha256")}


def vector_bytes(values):
    stream = BytesIO()
    np.save(stream, np.asarray(values, dtype="<f4"), allow_pickle=False)
    return stream.getvalue()


def checked_vectors(raw, count, dimension):
    if len(raw) > BATCH * dimension * 4 + 4096:
        raise RetrievalError("Oversized vector cache.")
    value = np.load(BytesIO(raw), allow_pickle=False)
    if value.dtype != np.dtype("float32") or value.shape != (count, dimension) or not np.isfinite(value).all():
        raise RetrievalError("Invalid vector shape, type or values.")
    if not np.allclose(np.linalg.norm(value, axis=1), 1, atol=1e-5):
        raise RetrievalError("Cached vectors are not unit normalized.")
    return value


def verify_points(client, collection, units, values):
    points = client.retrieve(collection, ids=[point_id(u["unit_id"]) for u in units],
                             with_payload=True, with_vectors=True)
    points = {str(p.id): p for p in points}
    for unit, vector in zip(units, values):
        point = points.get(point_id(unit["unit_id"]))
        if point is None or point.payload != payload(unit):
            raise RetrievalError("Qdrant point or source metadata is missing/changed.")
        actual = np.asarray(point.vector, dtype=np.float32)
        if actual.shape != vector.shape or not np.array_equal(actual, vector):
            raise RetrievalError("Qdrant vector differs from the verified embedding cache.")


def verify_collection(client, name, dimension, count=None):
    info = client.get_collection(name)
    vector = info.config.params.vectors
    if isinstance(vector, dict) or vector.size != dimension or vector.distance != qm.Distance.DOT:
        raise RetrievalError("Qdrant collection uses a different vector configuration.")
    if count is not None and client.count(name, exact=True).count != count:
        raise RetrievalError("Qdrant point count differs from the published index.")


def prepare_index(catalogue, models, client, *, index_dir=DEFAULT_INDEX,
                  data_dir=DATA_DIR, refresh=False, plain_text=False, progress=print):
    catalogue.replay(data_dir)
    if catalogue.warnings:
        raise RetrievalError("Resolve coverage warnings before building the index.")
    root = Path(index_dir)
    identity = {"passage_manifest_sha256": catalogue.manifest_sha, "models_sha256": models.fingerprint,
                "policy": POLICY, "plain_text": plain_text}
    generation = digest(_json_bytes(identity))
    name = "ad_filings_" + generation[:32]
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        old = bounded_json(manifest_path, 8 * 1024 * 1024)
        if old.get("identity") != identity and not refresh:
            raise RetrievalError("Corpus/model/settings changed. Use --refresh to publish a new index generation.")
    folder = root / "generations" / generation
    units = search_units(catalogue, models, plain_text=plain_text)
    ids = [point_id(u["unit_id"]) for u in units]
    if len(set(ids)) != len(ids):
        raise RetrievalError("Duplicate search-window identifier.")
    raw_units = _json_bytes(units)
    unit_sha = digest(raw_units)
    if (folder / "units.json").exists():
        if (folder / "units.json").read_bytes() != raw_units:
            raise RetrievalError("Staged search representations changed unexpectedly.")
    else:
        _atomic_write(folder / "units.json", raw_units)
    if not client.collection_exists(name):
        client.create_collection(name, vectors_config=qm.VectorParams(size=models.dimension, distance=qm.Distance.DOT))
        # Local Qdrant ignores these; server mode uses them for pre-filtered ANN search.
        if client.__class__.__name__ == "QdrantClient" and not client._client.__class__.__name__ == "QdrantLocal":
            for field, schema in (("ticker", qm.PayloadSchemaType.KEYWORD),
                                  ("fiscal_year", qm.PayloadSchemaType.INTEGER),
                                  ("item", qm.PayloadSchemaType.KEYWORD), ("kind", qm.PayloadSchemaType.KEYWORD)):
                client.create_payload_index(name, field, schema, wait=True)
    verify_collection(client, name, models.dimension)
    batches, embedded, reused = [], 0, 0
    for offset in range(0, len(units), BATCH):
        batch = units[offset:offset + BATCH]
        batch_key = digest(_json_bytes({"models": models.fingerprint, "units": batch}))
        file = folder / f"vectors_{offset:07d}.npy"
        receipt = folder / f"vectors_{offset:07d}.json"
        if receipt.exists():
            saved = bounded_json(receipt, 4096)
            if saved.get("batch_key") != batch_key or not file.is_file():
                raise RetrievalError("Embedding receipt does not match this batch.")
            raw = file.read_bytes()
            if digest(raw) != saved["sha256"]:
                raise RetrievalError("Embedding cache changed; do not reuse corrupted vectors.")
            values = checked_vectors(raw, len(batch), models.dimension)
            reused += len(batch)
        else:
            values = models.embed([u["text"] for u in batch])
            raw = vector_bytes(values)
            values = checked_vectors(raw, len(batch), models.dimension)
            saved = {"batch_key": batch_key, "sha256": digest(raw), "offset": offset, "count": len(batch), "file": file.name}
            _atomic_write(file, raw)
            _atomic_write(receipt, _json_bytes(saved))
            embedded += len(batch)
        # Skip already-correct batches. A missing/partial batch is repaired from
        # the verified embedding cache; no model request is needed for this retry.
        try:
            verify_points(client, name, batch, values)
        except RetrievalError:
            client.upsert(name, points=[qm.PointStruct(id=point_id(u["unit_id"]), vector=v.tolist(), payload=payload(u))
                                       for u, v in zip(batch, values)], wait=True)
            verify_points(client, name, batch, values)
        batches.append(saved)
        if progress:
            progress(f"Indexed and verified {min(offset + BATCH, len(units))}/{len(units)} search windows", flush=True)
    verify_collection(client, name, models.dimension, len(units))
    catalogue.verify_unchanged()
    manifest = {"schema_version": 1, "generation": generation, "collection": name,
                "identity": identity, "dimension": models.dimension, "units_sha256": unit_sha,
                "search_windows": len(units), "batches": batches, "coverage": catalogue.summary()}
    _atomic_write(manifest_path, _json_bytes(manifest))
    return {"status": "ok", "embedded_windows": embedded, "reused_windows": reused,
            "search_windows": len(units), "collection": name, "coverage": catalogue.summary(),
            "manifest": str(manifest_path)}


def load_index(catalogue, models_fingerprint, client, *, index_dir=DEFAULT_INDEX, verify_vectors=False):
    root = Path(index_dir)
    manifest = bounded_json(root / "manifest.json", 8 * 1024 * 1024)
    identity = manifest.get("identity", {})
    generation = digest(_json_bytes(identity))
    if (manifest.get("schema_version") != 1 or manifest.get("generation") != generation
            or manifest.get("collection") != "ad_filings_" + generation[:32]
            or identity.get("passage_manifest_sha256") != catalogue.manifest_sha
            or identity.get("models_sha256") != models_fingerprint or identity.get("policy") != POLICY):
        raise RetrievalError("Index does not match this corpus/model configuration. Rebuild and restart.")
    folder = root / "generations" / generation
    units = bounded_json(folder / "units.json", 256 * 1024 * 1024)
    raw = (folder / "units.json").read_bytes()
    if digest(raw) != manifest["units_sha256"] or len(units) != manifest["search_windows"]:
        raise RetrievalError("Search representations differ from the published index.")
    for unit in units:
        expected = "u_" + digest(_json_bytes({k: v for k, v in unit.items() if k != "unit_id"}))
        p = catalogue.passages.get(unit["passage_id"])
        if unit["unit_id"] != expected or p is None or any(unit[k] != p[k] for k in ("ticker", "fiscal_year", "item", "kind", "source_sha256")):
            raise RetrievalError("Invalid search-window identity or provenance.")
    verify_collection(client, manifest["collection"], manifest["dimension"], len(units))
    if verify_vectors:
        expected_offset = 0
        for batch in manifest["batches"]:
            if batch["offset"] != expected_offset or batch["file"] != f"vectors_{expected_offset:07d}.npy":
                raise RetrievalError("Invalid embedding batch order or path.")
            raw = (folder / batch["file"]).read_bytes()
            if digest(raw) != batch["sha256"]:
                raise RetrievalError("Embedding cache fingerprint mismatch.")
            values = checked_vectors(raw, batch["count"], manifest["dimension"])
            subset = units[expected_offset:expected_offset + batch["count"]]
            if batch["batch_key"] != digest(_json_bytes({"models": models_fingerprint, "units": subset})):
                raise RetrievalError("Embedding batch identity mismatch.")
            verify_points(client, manifest["collection"], subset, values)
            expected_offset += batch["count"]
        if expected_offset != len(units):
            raise RetrievalError("Embedding batches do not cover every search window.")
    return manifest, units
