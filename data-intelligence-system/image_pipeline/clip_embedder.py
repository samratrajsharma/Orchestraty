"""
CLIP Embedding Generator — produces image/text embeddings using OpenAI CLIP
and stores/queries vectors in Qdrant. Supports Intel XPU acceleration.
"""

import io
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache for loaded models
# ---------------------------------------------------------------------------
_MODEL_CACHE: dict[str, tuple] = {}


# ---------------------------------------------------------------------------
# Device detection (Intel XPU > CUDA > CPU)
# ---------------------------------------------------------------------------
def _get_device() -> str:
    try:
        import torch
        try:
            import intel_extension_for_pytorch  # noqa: F401
            if torch.xpu.is_available():
                return "xpu"
        except ImportError:
            pass
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
def load_clip_model(
    model_name: str = "openai/clip-vit-base-patch32",
) -> tuple:
    """
    Lazy-load CLIP model and processor, moving to the best available device.

    Returns:
        Tuple of (model, processor, device_str).
    """
    if model_name in _MODEL_CACHE:
        return _MODEL_CACHE[model_name]

    try:
        from transformers import CLIPModel, CLIPProcessor  # type: ignore
        import torch  # noqa: F811
    except ImportError as exc:
        raise ImportError(
            "transformers and torch are required for CLIP embeddings. "
            "Install with: pip install transformers torch"
        ) from exc

    device = _get_device()
    logger.info("Loading CLIP model '%s' on device '%s'", model_name, device)

    processor = CLIPProcessor.from_pretrained(model_name)
    model = CLIPModel.from_pretrained(model_name)
    model = model.to(device)
    model.eval()

    result = (model, processor, device)
    _MODEL_CACHE[model_name] = result
    return result


# ---------------------------------------------------------------------------
# Image embedding
# ---------------------------------------------------------------------------
def embed_images(
    image_bytes_list: list[bytes],
    model_name: str = "openai/clip-vit-base-patch32",
    batch_size: int = 32,
) -> np.ndarray:
    """
    Generate CLIP embeddings for a list of images.

    Args:
        image_bytes_list: List of raw image bytes.
        model_name: HuggingFace model identifier.
        batch_size: Number of images to process per batch.

    Returns:
        numpy array of shape (n_images, embedding_dim).
    """
    import torch
    from PIL import Image

    model, processor, device = load_clip_model(model_name)
    all_embeddings = []

    for i in range(0, len(image_bytes_list), batch_size):
        batch_bytes = image_bytes_list[i : i + batch_size]
        images = []
        for img_bytes in batch_bytes:
            try:
                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                images.append(img)
            except Exception as exc:
                logger.warning("Skipping invalid image in batch: %s", exc)
                continue

        if not images:
            continue

        inputs = processor(images=images, return_tensors="pt", padding=True)
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model.get_image_features(**inputs)
            # L2-normalize embeddings
            embeddings = outputs / outputs.norm(dim=-1, keepdim=True)
            all_embeddings.append(embeddings.cpu().numpy())

        logger.debug("Embedded batch %d-%d", i, i + len(images))

    if not all_embeddings:
        return np.empty((0, 512), dtype=np.float32)

    return np.concatenate(all_embeddings, axis=0)


# ---------------------------------------------------------------------------
# Text embedding (for text-to-image search)
# ---------------------------------------------------------------------------
def embed_text(
    text: str,
    model_name: str = "openai/clip-vit-base-patch32",
) -> np.ndarray:
    """
    Generate a CLIP text embedding for text-to-image search.

    Args:
        text: Query text string.
        model_name: HuggingFace model identifier.

    Returns:
        numpy array of shape (1, embedding_dim).
    """
    import torch

    model, processor, device = load_clip_model(model_name)

    inputs = processor(text=[text], return_tensors="pt", padding=True)
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model.get_text_features(**inputs)
        embedding = outputs / outputs.norm(dim=-1, keepdim=True)

    return embedding.cpu().numpy()


# ---------------------------------------------------------------------------
# Qdrant storage
# ---------------------------------------------------------------------------
def store_image_vectors_in_qdrant(
    vectors: np.ndarray,
    asset_ids: list[str],
    collection_name: str,
    metadata_list: Optional[list[dict]] = None,
) -> list[str]:
    """
    Upsert image vectors into a Qdrant collection.

    Creates the collection if it does not already exist (vector size 512,
    cosine distance for ViT-B/32).

    Args:
        vectors: numpy array of shape (n, 512).
        asset_ids: List of unique string IDs for each vector.
        collection_name: Qdrant collection name.
        metadata_list: Optional list of payload dicts per vector.

    Returns:
        List of Qdrant point IDs (same as asset_ids on success, empty on failure).
    """
    try:
        from qdrant_client import QdrantClient  # type: ignore
        from qdrant_client.models import Distance, VectorParams, PointStruct  # type: ignore
    except ImportError:
        logger.error("qdrant_client not installed — skipping vector storage")
        return []

    try:
        from core.settings import settings  # type: ignore

        client = QdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
        )
    except Exception as exc:
        logger.error("Failed to connect to Qdrant: %s", exc)
        return []

    dim = int(vectors.shape[1])

    # Create collection if it doesn't exist
    try:
        collections = [c.name for c in client.get_collections().collections]
        if collection_name not in collections:
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", collection_name, dim)
    except Exception as exc:
        logger.error("Failed to create collection: %s", exc)
        return []

    # Build points — use asset_ids as point IDs (UUIDs) to avoid collisions
    if metadata_list is None:
        metadata_list = [{} for _ in asset_ids]

    points = [
        PointStruct(
            id=asset_ids[idx],
            vector=vectors[idx].tolist(),
            payload={"asset_id": asset_ids[idx], **metadata_list[idx]},
        )
        for idx in range(len(asset_ids))
    ]

    # Upsert in batches
    try:
        batch_size = 100
        for i in range(0, len(points), batch_size):
            client.upsert(
                collection_name=collection_name,
                points=points[i : i + batch_size],
            )
        logger.info("Upserted %d vectors into '%s'", len(points), collection_name)
        return asset_ids
    except Exception as exc:
        logger.error("Qdrant upsert failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Similarity search
# ---------------------------------------------------------------------------
def search_similar(
    query_vector: np.ndarray,
    collection_name: str,
    top_k: int = 10,
) -> list[dict]:
    """
    Search for similar images in Qdrant by vector similarity.

    Args:
        query_vector: 1-D numpy array (embedding_dim,) or 2-D (1, dim).
        collection_name: Qdrant collection to search.
        top_k: Number of results to return.

    Returns:
        List of dicts with keys: id, score, payload.
    """
    try:
        from qdrant_client import QdrantClient  # type: ignore
    except ImportError:
        logger.error("qdrant_client not installed — cannot search")
        return []

    try:
        from core.settings import settings  # type: ignore

        client = QdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
        )
    except Exception as exc:
        logger.error("Failed to connect to Qdrant: %s", exc)
        return []

    # Flatten to 1-D if needed
    vec = query_vector.flatten().tolist()

    try:
        results = client.search(
            collection_name=collection_name,
            query_vector=vec,
            limit=top_k,
        )
        return [
            {"id": hit.id, "score": hit.score, "payload": hit.payload}
            for hit in results
        ]
    except Exception as exc:
        logger.error("Qdrant search failed: %s", exc)
        return []
