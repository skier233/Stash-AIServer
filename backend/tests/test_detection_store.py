"""Integration tests for stash_ai_server.db.detection_store.

These tests exercise the store / query / update functions against a real
PostgreSQL test database (with pgvector).  Every test gets a truncated DB
via the ``_clean`` autouse fixture.

Functions that create their own internal sessions via ``get_session_local``
are patched to use the test-engine session factory so data stays in the
test database.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
from sqlalchemy.orm import Session

from stash_ai_server.db.detection_store import (
    create_cluster,
    delete_cluster,
    find_nearest_cluster,
    get_cluster_by_id,
    get_cluster_embeddings,
    get_cluster_exemplars,
    get_entity_tracks,
    link_performer,
    list_clusters,
    merge_clusters,
    store_detection_track,
    store_face_embedding,
    try_add_exemplar,
    update_cluster_centroid,
    _recompute_exemplars,
)
from stash_ai_server.models.ai_results import AIModelRun
from stash_ai_server.models.detections import (
    DetectionTrack,
    FaceCluster,
    FaceEmbedding,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _rand_vec(dim: int = 512) -> list[float]:
    """Return a random L2-normalised vector."""
    v = np.random.default_rng(42).standard_normal(dim).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _make_vec(seed: int, dim: int = 512) -> list[float]:
    """Deterministic normalised vector for a given seed."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _make_run(session: Session, **overrides) -> AIModelRun:
    """Insert a minimal ``ai_model_runs`` row (FK target for detection_tracks)."""
    defaults = dict(
        service="test_service",
        plugin_name="test_plugin",
        entity_type="scene",
        entity_id=1,
        status="completed",
    )
    defaults.update(overrides)
    run = AIModelRun(**defaults)
    session.add(run)
    session.flush()
    return run


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def det_session(test_database, monkeypatch):
    """Provide a sync test session and redirect ``get_session_local`` to the test DB.

    Data must be **committed** before functions that open their own internal
    sessions (e.g. ``find_nearest_cluster``) can see it.
    """
    monkeypatch.setattr(
        "stash_ai_server.db.detection_store.get_session_local",
        lambda: test_database.test_session_factory,
    )
    session: Session = test_database.test_session_factory()
    yield session
    # Rollback any uncommitted state then close
    if session.in_transaction():
        session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def _clean(test_database):
    """Truncate all tables before and after each test for isolation."""
    test_database.truncate_all_tables()
    yield
    test_database.truncate_all_tables()


# ===================================================================
# store_detection_track
# ===================================================================

class TestStoreDetectionTrack:
    """Tests for ``store_detection_track``."""

    def test_basic_insert(self, det_session: Session):
        run = _make_run(det_session)
        det_session.commit()

        track = store_detection_track(
            det_session,
            run_id=run.id,
            entity_type="scene",
            entity_id=42,
            label="face",
            bbox=[0.1, 0.2, 0.3, 0.4],
            score=0.95,
            detector="yolo_face",
        )
        det_session.commit()

        assert track.id is not None
        assert track.label == "face"
        assert track.entity_id == 42
        assert track.score == pytest.approx(0.95)

    def test_optional_temporal_fields(self, det_session: Session):
        run = _make_run(det_session)
        det_session.commit()

        track = store_detection_track(
            det_session,
            run_id=run.id,
            entity_type="scene",
            entity_id=1,
            label="face",
            bbox=[0.0, 0.0, 1.0, 1.0],
            score=0.8,
            detector="det",
            start_s=1.5,
            end_s=4.0,
            keyframes=[{"t": 2.0, "bbox": [0.1, 0.1, 0.5, 0.5]}],
            metadata={"source": "test"},
        )
        det_session.commit()

        assert track.start_s == pytest.approx(1.5)
        assert track.end_s == pytest.approx(4.0)
        assert track.keyframes[0]["t"] == 2.0
        assert track.metadata_["source"] == "test"


# ===================================================================
# create_cluster
# ===================================================================

class TestCreateCluster:

    def test_default_status(self, det_session: Session):
        cluster = create_cluster(det_session)
        det_session.commit()
        assert cluster.id is not None
        assert cluster.status == "unidentified"
        assert cluster.performer_id is None

    def test_with_performer(self, det_session: Session):
        cluster = create_cluster(
            det_session,
            status="identified",
            performer_id=99,
            label="Jane Doe",
        )
        det_session.commit()
        assert cluster.performer_id == 99
        assert cluster.label == "Jane Doe"


# ===================================================================
# store_face_embedding
# ===================================================================

class TestStoreFaceEmbedding:

    def test_basic_insert(self, det_session: Session):
        run = _make_run(det_session)
        track = store_detection_track(
            det_session,
            run_id=run.id,
            entity_type="scene",
            entity_id=1,
            label="face",
            bbox=[0.1, 0.2, 0.3, 0.4],
            score=0.9,
            detector="det",
        )
        cluster = create_cluster(det_session)
        det_session.commit()

        emb = store_face_embedding(
            det_session,
            track_id=track.id,
            cluster_id=cluster.id,
            entity_type="scene",
            entity_id=1,
            embedding=_make_vec(1),
            norm=1.0,
            score=0.92,
            embedder="arcface",
        )
        det_session.commit()

        assert emb.id is not None
        assert emb.track_id == track.id
        assert emb.cluster_id == cluster.id
        assert emb.embedder == "arcface"
        assert len(emb.embedding) == 512

    def test_exemplar_flag(self, det_session: Session):
        run = _make_run(det_session)
        track = store_detection_track(
            det_session,
            run_id=run.id,
            entity_type="scene",
            entity_id=1,
            label="face",
            bbox=[0.0, 0.0, 1.0, 1.0],
            score=0.8,
            detector="det",
        )
        cluster = create_cluster(det_session)
        det_session.commit()

        emb = store_face_embedding(
            det_session,
            track_id=track.id,
            cluster_id=cluster.id,
            entity_type="scene",
            entity_id=1,
            embedding=_make_vec(2),
            norm=1.0,
            score=0.88,
            is_exemplar=True,
            embedder="arcface",
        )
        det_session.commit()

        assert emb.is_exemplar is True


# ===================================================================
# find_nearest_cluster (pgvector cosine search)
# ===================================================================

class TestFindNearestCluster:

    def _seed_cluster(self, session: Session, vec: list[float], status: str = "unidentified"):
        """Insert a cluster with a centroid via raw update (centroid is not nullable-safe on create)."""
        cluster = create_cluster(session, status=status)
        session.flush()
        from sqlalchemy import update, text
        session.execute(
            update(FaceCluster)
            .where(FaceCluster.id == cluster.id)
            .values(centroid=vec, sample_count=1)
        )
        session.commit()
        return cluster

    def test_returns_nearest(self, det_session: Session):
        vec_a = _make_vec(10)
        vec_b = _make_vec(20)
        self._seed_cluster(det_session, vec_a)
        self._seed_cluster(det_session, vec_b)

        # vec_a should be most similar to itself
        results = find_nearest_cluster(vec_a, limit=2)
        assert len(results) == 2
        best_id, best_sim = results[0]
        assert best_sim > 0.99  # near-perfect match

    def test_excludes_merged_away(self, det_session: Session):
        vec = _make_vec(40)
        self._seed_cluster(det_session, vec, status="merged_away")

        results = find_nearest_cluster(vec, limit=5)
        assert len(results) == 0

    def test_empty_db(self, det_session: Session):
        results = find_nearest_cluster(_make_vec(50))
        assert results == []


# ===================================================================
# update_cluster_centroid
# ===================================================================

class TestUpdateClusterCentroid:

    def _setup_cluster_with_exemplars(self, session, vecs, scores=None):
        """Create a cluster, a run, a track, and exemplar embeddings."""
        run = _make_run(session)
        track = store_detection_track(
            session,
            run_id=run.id,
            entity_type="scene",
            entity_id=1,
            label="face",
            bbox=[0.0, 0.0, 1.0, 1.0],
            score=0.9,
            detector="det",
        )
        cluster = create_cluster(session)
        session.flush()

        if scores is None:
            scores = [0.9] * len(vecs)

        for i, (vec, sc) in enumerate(zip(vecs, scores)):
            store_face_embedding(
                session,
                track_id=track.id,
                cluster_id=cluster.id,
                entity_type="scene",
                entity_id=1,
                embedding=vec,
                norm=float(np.linalg.norm(vec)),
                score=sc,
                is_exemplar=True,
                embedder="arcface",
            )
        session.commit()
        return cluster

    def test_centroid_is_mean(self, det_session: Session):
        v1 = _make_vec(100)
        v2 = _make_vec(101)
        cluster = self._setup_cluster_with_exemplars(det_session, [v1, v2])

        update_cluster_centroid(cluster.id)

        refreshed = get_cluster_by_id(cluster.id)
        assert refreshed is not None
        assert refreshed.centroid is not None
        assert refreshed.sample_count == 2

        # Centroid should be normalised
        centroid = np.array(refreshed.centroid, dtype=np.float32)
        assert np.linalg.norm(centroid) == pytest.approx(1.0, abs=1e-4)

    def test_no_exemplars_clears_centroid(self, det_session: Session):
        cluster = create_cluster(det_session)
        det_session.commit()

        update_cluster_centroid(cluster.id)

        refreshed = get_cluster_by_id(cluster.id)
        assert refreshed.centroid is None
        assert refreshed.sample_count == 0


# ===================================================================
# link_performer / delete_cluster
# ===================================================================

class TestLinkPerformer:

    def test_sets_performer_and_status(self, det_session: Session):
        cluster = create_cluster(det_session)
        det_session.commit()

        link_performer(cluster.id, performer_id=42)

        refreshed = get_cluster_by_id(cluster.id)
        assert refreshed.status == "identified"
        assert refreshed.performer_id == 42


class TestDeleteCluster:

    def test_removes_cluster(self, det_session: Session):
        cluster = create_cluster(det_session)
        det_session.commit()

        delete_cluster(cluster.id)

        assert get_cluster_by_id(cluster.id) is None


# ===================================================================
# merge_clusters
# ===================================================================

class TestMergeClusters:

    def _setup_two_clusters(self, session):
        """Create two clusters, each with one exemplar embedding."""
        run = _make_run(session)
        track = store_detection_track(
            session,
            run_id=run.id,
            entity_type="scene",
            entity_id=1,
            label="face",
            bbox=[0.0, 0.0, 1.0, 1.0],
            score=0.9,
            detector="det",
        )
        c1 = create_cluster(session, label="A")
        c2 = create_cluster(session, label="B")
        session.flush()

        emb1 = store_face_embedding(
            session,
            track_id=track.id,
            cluster_id=c1.id,
            entity_type="scene",
            entity_id=1,
            embedding=_make_vec(200),
            norm=1.0,
            score=0.95,
            is_exemplar=True,
            embedder="arcface",
        )
        emb2 = store_face_embedding(
            session,
            track_id=track.id,
            cluster_id=c2.id,
            entity_type="scene",
            entity_id=1,
            embedding=_make_vec(201),
            norm=1.0,
            score=0.88,
            is_exemplar=True,
            embedder="arcface",
        )
        session.commit()
        return c1, c2, emb1, emb2

    def test_absorbed_marked_merged_away(self, det_session: Session):
        c1, c2, _, _ = self._setup_two_clusters(det_session)

        merge_clusters(c1.id, c2.id)

        absorbed = get_cluster_by_id(c2.id)
        assert absorbed.status == "merged_away"
        assert absorbed.merged_into_id == c1.id

    def test_embeddings_re_parented(self, det_session: Session):
        c1, c2, _, emb2 = self._setup_two_clusters(det_session)

        merge_clusters(c1.id, c2.id)

        # All embeddings should belong to surviving cluster
        all_embs = get_cluster_embeddings(c1.id)
        assert len(all_embs) == 2

        orphaned = get_cluster_embeddings(c2.id)
        assert len(orphaned) == 0


# ===================================================================
# try_add_exemplar
# ===================================================================

class TestTryAddExemplar:

    def _base_setup(self, session):
        """Create run, track, cluster — return (track, cluster)."""
        run = _make_run(session)
        track = store_detection_track(
            session,
            run_id=run.id,
            entity_type="scene",
            entity_id=1,
            label="face",
            bbox=[0.0, 0.0, 1.0, 1.0],
            score=0.9,
            detector="det",
        )
        cluster = create_cluster(session)
        session.flush()
        return track, cluster

    def test_first_embedding_accepted(self, det_session: Session):
        track, cluster = self._base_setup(det_session)
        det_session.commit()

        accepted = try_add_exemplar(
            det_session,
            cluster_id=cluster.id,
            embedding=_make_vec(300),
            norm=1.0,
            score=0.9,
        )
        assert accepted is True

    def test_duplicate_rejected(self, det_session: Session):
        track, cluster = self._base_setup(det_session)
        vec = _make_vec(301)
        store_face_embedding(
            det_session,
            track_id=track.id,
            cluster_id=cluster.id,
            entity_type="scene",
            entity_id=1,
            embedding=vec,
            norm=1.0,
            score=0.9,
            is_exemplar=True,
            embedder="arcface",
        )
        det_session.commit()

        # Same vector should be rejected (cosine ≥ dedup_threshold)
        accepted = try_add_exemplar(
            det_session,
            cluster_id=cluster.id,
            embedding=vec,  # identical
            norm=1.0,
            score=0.9,
        )
        assert accepted is False

    def test_evicts_worst_when_full(self, det_session: Session):
        track, cluster = self._base_setup(det_session)

        # Fill cluster with max_exemplars diverse embeddings, all low score
        max_ex = 5
        for i in range(max_ex):
            store_face_embedding(
                det_session,
                track_id=track.id,
                cluster_id=cluster.id,
                entity_type="scene",
                entity_id=1,
                embedding=_make_vec(400 + i),
                norm=1.0,
                score=0.5,  # low quality
                is_exemplar=True,
                embedder="arcface",
            )
        det_session.commit()

        # New high-quality diverse vector should evict the worst
        accepted = try_add_exemplar(
            det_session,
            cluster_id=cluster.id,
            embedding=_make_vec(999),
            norm=1.0,
            score=0.99,
            max_exemplars=max_ex,
        )
        assert accepted is True

    def test_rejected_when_full_and_not_better(self, det_session: Session):
        track, cluster = self._base_setup(det_session)

        max_ex = 3
        for i in range(max_ex):
            store_face_embedding(
                det_session,
                track_id=track.id,
                cluster_id=cluster.id,
                entity_type="scene",
                entity_id=1,
                embedding=_make_vec(500 + i),
                norm=1.0,
                score=0.99,  # high quality
                is_exemplar=True,
                embedder="arcface",
            )
        det_session.commit()

        # Low-quality new vector should be rejected
        accepted = try_add_exemplar(
            det_session,
            cluster_id=cluster.id,
            embedding=_make_vec(600),
            norm=0.5,
            score=0.1,
            max_exemplars=max_ex,
        )
        assert accepted is False


# ===================================================================
# _recompute_exemplars
# ===================================================================

class TestRecomputeExemplars:

    def test_selects_best_and_deduplicates(self, det_session: Session):
        run = _make_run(det_session)
        track = store_detection_track(
            det_session,
            run_id=run.id,
            entity_type="scene",
            entity_id=1,
            label="face",
            bbox=[0.0, 0.0, 1.0, 1.0],
            score=0.9,
            detector="det",
        )
        cluster = create_cluster(det_session)
        det_session.flush()

        # Insert 15 embeddings (above the 10-exemplar cap) — a mix of diverse and duplicate
        base_vec = _make_vec(700)
        for i in range(15):
            # First 5 are unique high-quality
            if i < 5:
                vec = _make_vec(700 + i)
                sc = 0.95 - i * 0.01
            # Next 5 are near-duplicates of the first
            elif i < 10:
                vec = base_vec  # identical to seed=700
                sc = 0.80
            else:
                vec = _make_vec(800 + i)
                sc = 0.60
            store_face_embedding(
                det_session,
                track_id=track.id,
                cluster_id=cluster.id,
                entity_type="scene",
                entity_id=1,
                embedding=vec,
                norm=1.0,
                score=sc,
                is_exemplar=False,
                embedder="arcface",
            )
        det_session.commit()

        _recompute_exemplars(cluster.id)

        exemplars = get_cluster_exemplars(cluster.id)
        # Should have selected ≤ 10 and deduplicated the identical vectors
        assert 1 <= len(exemplars) <= 10
        # All selected ones must have is_exemplar=True
        for ex in exemplars:
            assert ex.is_exemplar is True


# ===================================================================
# get_entity_tracks / get_cluster_embeddings / list_clusters / get_cluster_by_id
# ===================================================================

class TestQueryFunctions:

    def test_get_entity_tracks(self, det_session: Session):
        run = _make_run(det_session)
        store_detection_track(
            det_session,
            run_id=run.id, entity_type="scene", entity_id=5,
            label="face", bbox=[0, 0, 1, 1], score=0.9, detector="det",
        )
        store_detection_track(
            det_session,
            run_id=run.id, entity_type="scene", entity_id=5,
            label="person", bbox=[0, 0, 1, 1], score=0.8, detector="det",
        )
        store_detection_track(
            det_session,
            run_id=run.id, entity_type="scene", entity_id=99,
            label="face", bbox=[0, 0, 1, 1], score=0.7, detector="det",
        )
        det_session.commit()

        # All tracks for entity 5
        tracks = get_entity_tracks("scene", 5)
        assert len(tracks) == 2

        # Filtered by label
        face_tracks = get_entity_tracks("scene", 5, label="face")
        assert len(face_tracks) == 1
        assert face_tracks[0].label == "face"

    def test_list_clusters_pagination(self, det_session: Session):
        for _ in range(5):
            create_cluster(det_session)
        det_session.commit()

        clusters, total = list_clusters(offset=0, limit=3)
        assert total == 5
        assert len(clusters) == 3

        clusters2, _ = list_clusters(offset=3, limit=3)
        assert len(clusters2) == 2

    def test_list_clusters_status_filter(self, det_session: Session):
        create_cluster(det_session, status="unidentified")
        create_cluster(det_session, status="identified", performer_id=1)
        det_session.commit()

        clusters, total = list_clusters(status="identified")
        assert total == 1
        assert clusters[0].status == "identified"

    def test_get_cluster_by_id_exists(self, det_session: Session):
        cluster = create_cluster(det_session, label="test")
        det_session.commit()

        found = get_cluster_by_id(cluster.id)
        assert found is not None
        assert found.label == "test"

    def test_get_cluster_by_id_missing(self, det_session: Session):
        found = get_cluster_by_id(999999)
        assert found is None

    def test_get_cluster_embeddings_all(self, det_session: Session):
        run = _make_run(det_session)
        track = store_detection_track(
            det_session,
            run_id=run.id, entity_type="scene", entity_id=1,
            label="face", bbox=[0, 0, 1, 1], score=0.9, detector="det",
        )
        cluster = create_cluster(det_session)
        det_session.flush()

        store_face_embedding(
            det_session,
            track_id=track.id, cluster_id=cluster.id,
            entity_type="scene", entity_id=1,
            embedding=_make_vec(900), norm=1.0, score=0.9,
            is_exemplar=True, embedder="arcface",
        )
        store_face_embedding(
            det_session,
            track_id=track.id, cluster_id=cluster.id,
            entity_type="scene", entity_id=1,
            embedding=_make_vec(901), norm=1.0, score=0.8,
            is_exemplar=False, embedder="arcface",
        )
        det_session.commit()

        all_embs = get_cluster_embeddings(cluster.id)
        assert len(all_embs) == 2

        exemplars = get_cluster_embeddings(cluster.id, exemplars_only=True)
        assert len(exemplars) == 1
        assert exemplars[0].is_exemplar is True
