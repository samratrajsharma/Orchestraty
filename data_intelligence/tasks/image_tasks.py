"""
Celery tasks for async image processing.
Handles embedding generation and image clustering as background jobs.
"""

import io
import logging

import core.paths  # noqa: F401

from celery import current_task

from core.celery_app import app
from core.database_sync import SyncSessionLocal
from core.services.task_service import update_task_progress, complete_task

logger = logging.getLogger(__name__)


def _get_minio_client():
    """Create and return a MinIO client using app settings."""
    from core.storage import get_minio_client
    return get_minio_client()


@app.task(bind=True, name="image.generate_embeddings")
def generate_image_embeddings(self, dataset_id: str):
    """Generate CLIP embeddings for all unprocessed images in a dataset.

    Args:
        dataset_id: UUID of the dataset whose images to embed.
    """
    celery_task_id = self.request.id
    db = SyncSessionLocal()

    try:
        # ── Step 1: Find images without embeddings ──────────────────────
        update_task_progress(db, celery_task_id, 0.05, "Querying images...")

        from core.models.image_asset import ImageAsset

        images = (
            db.query(ImageAsset)
            .filter(
                ImageAsset.dataset_id == dataset_id,
                ImageAsset.embedding_id.is_(None),
            )
            .all()
        )

        if not images:
            complete_task(
                db, celery_task_id,
                result={"message": "No unprocessed images found", "count": 0},
            )
            return {"message": "No unprocessed images found", "count": 0}

        total = len(images)
        logger.info(
            "Found %d images without embeddings for dataset %s",
            total, dataset_id,
        )

        # ── Step 2: Download images from MinIO in batches ───────────────
        update_task_progress(
            db, celery_task_id, 0.1, f"Downloading {total} images from storage...",
        )

        from core.settings import settings

        client = _get_minio_client()
        batch_size = 32
        all_embedding_ids = []

        from image_pipeline.clip_embedder import embed_images, store_image_vectors_in_qdrant

        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch_images = images[batch_start:batch_end]

            # Download image bytes for this batch
            image_bytes_list = []
            image_ids = []
            for img in batch_images:
                response = client.get_object(
                    settings.MINIO_BUCKET_NAME, img.original_path,
                )
                raw = response.read()
                response.close()
                response.release_conn()
                image_bytes_list.append(raw)
                image_ids.append(str(img.id))

            # ── Step 3: Generate embeddings ─────────────────────────────
            progress = 0.1 + 0.5 * (batch_end / total)
            update_task_progress(
                db, celery_task_id, progress,
                f"Generating embeddings ({batch_end}/{total})...",
            )

            embeddings = embed_images(image_bytes_list)

            # ── Step 4: Store vectors in Qdrant ─────────────────────────
            collection_name = f"{dataset_id}_images"
            progress = 0.1 + 0.7 * (batch_end / total)
            update_task_progress(
                db, celery_task_id, progress,
                f"Storing vectors in Qdrant ({batch_end}/{total})...",
            )

            stored_ids = store_image_vectors_in_qdrant(
                embeddings, image_ids, collection_name,
            )
            if not stored_ids:
                raise RuntimeError(
                    f"Qdrant upsert failed for batch {batch_start}-{batch_end}"
                )
            all_embedding_ids.extend(zip(batch_images, stored_ids))

        # ── Step 5: Update ImageAsset records ───────────────────────────
        update_task_progress(
            db, celery_task_id, 0.9, "Updating database records...",
        )

        for img, emb_id in all_embedding_ids:
            img.embedding_id = str(emb_id)
        db.commit()

        # ── Done ────────────────────────────────────────────────────────
        result = {
            "dataset_id": dataset_id,
            "images_processed": total,
            "collection": f"{dataset_id}_images",
        }
        complete_task(db, celery_task_id, result=result)
        logger.info(
            "Embedding generation complete for dataset %s: %d images processed",
            dataset_id, total,
        )
        return result

    except Exception as exc:
        logger.error(
            "Embedding generation failed for dataset %s: %s",
            dataset_id, exc, exc_info=True,
        )
        complete_task(db, celery_task_id, error=str(exc))
        raise
    finally:
        db.close()


@app.task(bind=True, name="image.run_clustering")
def run_image_clustering(self, dataset_id: str, min_cluster_size: int = 5):
    """Cluster images in a dataset based on their CLIP embeddings.

    Args:
        dataset_id: UUID of the dataset whose images to cluster.
        min_cluster_size: Minimum cluster size for HDBSCAN.
    """
    celery_task_id = self.request.id
    db = SyncSessionLocal()

    try:
        # ── Step 1: Fetch vectors from Qdrant ───────────────────────────
        update_task_progress(
            db, celery_task_id, 0.1, "Fetching vectors from Qdrant...",
        )

        from qdrant_client import QdrantClient
        from core.settings import settings

        qdrant = QdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
        )

        collection_name = f"{dataset_id}_images"
        all_points = []
        offset = None

        # Scroll through all points in the collection
        while True:
            results, next_offset = qdrant.scroll(
                collection_name=collection_name,
                limit=256,
                offset=offset,
                with_vectors=True,
            )
            all_points.extend(results)
            if next_offset is None:
                break
            offset = next_offset

        if len(all_points) < min_cluster_size:
            result = {
                "warning": "Too few vectors for clustering",
                "vector_count": len(all_points),
                "min_cluster_size": min_cluster_size,
            }
            complete_task(db, celery_task_id, result=result)
            return result

        logger.info(
            "Fetched %d vectors for dataset %s", len(all_points), dataset_id,
        )

        # ── Step 2: Run clustering ──────────────────────────────────────
        update_task_progress(
            db, celery_task_id, 0.4,
            f"Clustering {len(all_points)} images...",
        )

        import numpy as np
        from image_pipeline.image_clusterer import cluster_images

        vectors = np.array([p.vector for p in all_points])
        point_ids = [str(p.id) for p in all_points]

        cluster_report = cluster_images(vectors, min_cluster_size)

        # ── Step 3: Update ImageAsset cluster IDs ───────────────────────
        update_task_progress(
            db, celery_task_id, 0.75, "Updating cluster assignments...",
        )

        from core.models.image_asset import ImageAsset

        labels = cluster_report.labels
        for point_id, label in zip(point_ids, labels):
            db.query(ImageAsset).filter(
                ImageAsset.id == point_id,
            ).update({"cluster_id": int(label)})
        db.commit()

        # ── Done ────────────────────────────────────────────────────────
        result = {
            "dataset_id": dataset_id,
            "total_images": len(all_points),
            "clusters_found": cluster_report.n_clusters,
            "noise_points": cluster_report.noise_count,
            "cluster_sizes": cluster_report.cluster_sizes,
        }
        complete_task(db, celery_task_id, result=result)
        logger.info(
            "Clustering complete for dataset %s: %d clusters found",
            dataset_id, cluster_report.n_clusters,
        )
        return result

    except Exception as exc:
        logger.error(
            "Clustering failed for dataset %s: %s",
            dataset_id, exc, exc_info=True,
        )
        complete_task(db, celery_task_id, error=str(exc))
        raise
    finally:
        db.close()
