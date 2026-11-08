import logging
import json
import os
from functools import lru_cache
from pathlib import Path
from threading import Lock

from .common import DATA, read_products


logger = logging.getLogger(__name__)
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
INDEX_DIR = Path(__file__).resolve().parents[1] / ".cache"
INDEX_PATH = INDEX_DIR / "products.faiss"
METADATA_PATH = INDEX_DIR / "products.json"
_INDEX_LOCK = Lock()
_INDEX_READY = False


@lru_cache(maxsize=1)
def _build_index():
    """Load a persisted catalog index or build and save it once."""
    try:
        import faiss
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.warning("Content recommendations disabled; install dependencies from requirements.txt")
        return None

    products = read_products()
    product_ids = products.index.tolist()
    source_path = DATA / "product_master.parquet"
    source_stat = source_path.stat()
    fingerprint = {
        "model": MODEL_NAME,
        "source_size": source_stat.st_size,
        "source_mtime_ns": source_stat.st_mtime_ns,
        "product_ids": product_ids,
    }

    with _INDEX_LOCK:
        if INDEX_PATH.exists() and METADATA_PATH.exists():
            try:
                with METADATA_PATH.open(encoding="utf-8") as file:
                    cached = json.load(file)
                if all(cached.get(key) == value for key, value in fingerprint.items()):
                    return faiss.read_index(str(INDEX_PATH)), product_ids
            except (json.JSONDecodeError, OSError, RuntimeError):
                logger.warning("Ignoring unreadable cached recommendation index")

        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        text = (
            products["product_name"].fillna("")
            + " "
            + products["product_description"].fillna("")
            + " "
            + products["product_attributes"].map(
                lambda value: " ".join(f"{key} {item}" for key, item in value.items())
            )
        ).tolist()
        embeddings = SentenceTransformer(MODEL_NAME).encode(
            text, batch_size=64, normalize_embeddings=True, show_progress_bar=False
        )
        vectors = np.asarray(embeddings, dtype="float32")
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)

        temporary_index = INDEX_DIR / f".products.{os.getpid()}.faiss.tmp"
        temporary_metadata = INDEX_DIR / f".products.{os.getpid()}.json.tmp"
        faiss.write_index(index, str(temporary_index))
        with temporary_metadata.open("w", encoding="utf-8") as file:
            json.dump(fingerprint, file)
        os.replace(temporary_index, INDEX_PATH)
        os.replace(temporary_metadata, METADATA_PATH)
        return index, product_ids


def warm_index() -> None:
    """Build/load the content index outside a request thread."""
    global _INDEX_READY
    _INDEX_READY = _build_index() is not None


def get_similar_products(
    product_id: str,
    limit: int = 30,
    exclude_product_ids: set[str] | None = None,
) -> list[dict]:
    """Retrieve products nearest to the selected item in the FAISS index."""
    if not _INDEX_READY:
        return []
    built = _build_index()
    if built is None:
        return []
    index, product_ids = built
    products = read_products()
    if product_id not in products.index:
        return []
    row = product_ids.index(product_id)
    query = index.reconstruct(row).reshape(1, -1)
    search_limit = min(max(limit * 8 + 1, limit + 1), len(product_ids))
    scores, indices = index.search(query, search_limit)
    output = []
    maximum = float(scores[0][0]) if len(scores[0]) else 0.0
    for score, index_value in zip(scores[0], indices[0]):
        if index_value < 0:
            continue
        similar_id = product_ids[index_value]
        if similar_id == product_id:
            continue
        output.append(
            {
                "product_id": similar_id,
                "score": float(score / maximum) if maximum else 0.0,
                "source": "content",
                "reason": "Similar style and product details",
            }
        )
    excluded = {product_id} | (exclude_product_ids or set())
    return [item for item in output if item["product_id"] not in excluded][:limit]
