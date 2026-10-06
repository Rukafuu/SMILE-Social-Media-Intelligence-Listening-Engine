"""SQLite repository. Checkpoints and post writes share one transaction."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from app.models import Post


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Repository:
    def __init__(self, database_path: str) -> None:
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY,
                platform TEXT NOT NULL,
                post_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                content TEXT NOT NULL,
                normalized_content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                author_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                likes INTEGER, comments INTEGER, reposts INTEGER, views INTEGER,
                canonical_uri TEXT, repost_of TEXT, cited_source TEXT,
                event_published_at TEXT, is_synthetic INTEGER NOT NULL,
                UNIQUE(platform, post_id)
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                source_key TEXT PRIMARY KEY,
                cursor TEXT,
                exhausted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS collection_runs (
                id INTEGER PRIMARY KEY,
                source_key TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                pages INTEGER NOT NULL DEFAULT 0,
                inserted_posts INTEGER NOT NULL DEFAULT 0,
                coverage TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS invalid_records (
                id INTEGER PRIMARY KEY,
                source_key TEXT NOT NULL,
                cursor TEXT,
                payload TEXT NOT NULL,
                error TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS topics (
                topic_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                primary_category TEXT,
                secondary_categories TEXT NOT NULL DEFAULT '[]',
                classification_method TEXT NOT NULL,
                classification_reason TEXT NOT NULL,
                taxonomy_version TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS topic_windows (
                topic_id TEXT NOT NULL REFERENCES topics(topic_id),
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                post_count INTEGER NOT NULL,
                capped_contributions INTEGER NOT NULL,
                author_count INTEGER NOT NULL,
                family_count INTEGER NOT NULL,
                hhi REAL,
                baseline REAL,
                score REAL,
                stage TEXT NOT NULL,
                components TEXT NOT NULL,
                PRIMARY KEY(topic_id, window_start, window_end)
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY,
                topic_id TEXT NOT NULL REFERENCES topics(topic_id),
                analysis_mode TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY,
                alert_id INTEGER NOT NULL REFERENCES alerts(id),
                decision TEXT NOT NULL CHECK(decision IN ('APPROVE', 'REJECT', 'EDIT')),
                revised_summary TEXT,
                reviewer TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(alert_id, decision, reviewer, created_at)
            );
            """
        )
        # Compatible migration for SQLite databases created by earlier MVP steps.
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(collection_runs)")}
        if "coverage" not in columns:
            self.connection.execute("ALTER TABLE collection_runs ADD COLUMN coverage TEXT")
        self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def get_checkpoint(self, source_key: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT source_key, cursor, exhausted, updated_at FROM checkpoints WHERE source_key = ?", (source_key,)
        ).fetchone()

    def start_run(self, source_key: str) -> int:
        # A previous local process may have been interrupted after committing a
        # page. Preserve its checkpoint and mark the run honestly before retry.
        self.connection.execute(
            """UPDATE collection_runs SET finished_at=?, status='interrupted', error='process interrupted before run completion'
               WHERE source_key=? AND status='running'""",
            (utc_now().isoformat(), source_key),
        )
        cursor = self.connection.execute(
            "INSERT INTO collection_runs (source_key, started_at, status) VALUES (?, ?, 'running')",
            (source_key, utc_now().isoformat()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, pages: int, inserted: int, error: Optional[str] = None,
                   coverage: Optional[dict] = None) -> None:
        self.connection.execute(
            "UPDATE collection_runs SET finished_at=?, status=?, pages=?, inserted_posts=?, coverage=?, error=? WHERE id=?",
            (utc_now().isoformat(), status, pages, inserted, json.dumps(coverage or {}), error, run_id),
        )
        self.connection.commit()

    def persist_page(self, source_key: str, next_cursor: Optional[str], exhausted: bool, posts: Iterable[Post]) -> int:
        inserted = 0
        with self.transaction() as connection:
            for post in posts:
                normalized = " ".join(post.content.casefold().split())
                content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                result = connection.execute(
                    """INSERT OR IGNORE INTO posts (
                        platform, post_id, timestamp, collected_at, content, normalized_content, content_hash,
                        author_id, source_url, likes, comments, reposts, views, canonical_uri, repost_of,
                        cited_source, event_published_at, is_synthetic
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (post.platform, post.post_id, post.timestamp.isoformat(), post.collected_at.isoformat(),
                     post.content, normalized, content_hash, post.author_id, post.source_url, post.likes,
                     post.comments, post.reposts, post.views, post.canonical_uri, post.repost_of,
                     post.cited_source, post.event_published_at.isoformat() if post.event_published_at else None,
                     int(post.is_synthetic)),
                )
                inserted += result.rowcount
            connection.execute(
                """INSERT INTO checkpoints (source_key, cursor, exhausted, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(source_key) DO UPDATE SET cursor=excluded.cursor, exhausted=excluded.exhausted,
                   updated_at=excluded.updated_at""",
                (source_key, next_cursor, int(exhausted), utc_now().isoformat()),
            )
        return inserted

    def record_invalid(self, source_key: str, cursor: Optional[str], payload: object, error: str) -> None:
        self.connection.execute(
            "INSERT INTO invalid_records (source_key, cursor, payload, error, created_at) VALUES (?, ?, ?, ?, ?)",
            (source_key, cursor, json.dumps(payload, ensure_ascii=False), error, utc_now().isoformat()),
        )
        self.connection.commit()

    def post_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0])

    def posts_between(self, start: str, end: str):
        return self.connection.execute(
            "SELECT * FROM posts WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp", (start, end)
        ).fetchall()

    def upsert_topic(self, topic_id: str, title: str, primary_category: Optional[str], secondary_categories: list,
                     reason: str, taxonomy_version: str, first_seen: str, last_seen: str) -> None:
        self.connection.execute(
            """INSERT INTO topics (topic_id, title, primary_category, secondary_categories, classification_method,
               classification_reason, taxonomy_version, first_seen_at, last_seen_at, updated_at)
               VALUES (?, ?, ?, ?, 'deterministic_rules', ?, ?, ?, ?, ?)
               ON CONFLICT(topic_id) DO UPDATE SET title=excluded.title, primary_category=excluded.primary_category,
               secondary_categories=excluded.secondary_categories, classification_reason=excluded.classification_reason,
               taxonomy_version=excluded.taxonomy_version, last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at""",
            (topic_id, title, primary_category, json.dumps(secondary_categories), reason, taxonomy_version,
             first_seen, last_seen, utc_now().isoformat()),
        )
        self.connection.commit()

    def save_topic_window(self, topic_id: str, window_start: str, window_end: str, metrics: dict) -> None:
        self.connection.execute(
            """INSERT INTO topic_windows (topic_id, window_start, window_end, post_count, capped_contributions,
               author_count, family_count, hhi, baseline, score, stage, components)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(topic_id, window_start, window_end) DO UPDATE SET post_count=excluded.post_count,
               capped_contributions=excluded.capped_contributions, author_count=excluded.author_count,
               family_count=excluded.family_count, hhi=excluded.hhi, baseline=excluded.baseline, score=excluded.score,
               stage=excluded.stage, components=excluded.components""",
            (topic_id, window_start, window_end, metrics["N"], metrics["n"], metrics["U"], metrics["F"],
             metrics["HHI"], metrics["baseline"], metrics["score"], metrics["stage"],
             json.dumps(metrics["components"])),
        )
        self.connection.commit()

    def latest_topic_metrics(self, topic_id: str):
        return self.connection.execute(
            "SELECT * FROM topic_windows WHERE topic_id=? ORDER BY window_end DESC LIMIT 1", (topic_id,)
        ).fetchone()

    def save_alert(self, topic_id: str, analysis_mode: str, recommendation: str, payload: dict) -> int:
        result = self.connection.execute(
            "INSERT INTO alerts (topic_id, analysis_mode, recommendation, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (topic_id, analysis_mode, recommendation, json.dumps(payload, ensure_ascii=False), utc_now().isoformat()),
        )
        self.connection.commit()
        return int(result.lastrowid)

    def save_review(self, alert_id: int, decision: str, revised_summary: Optional[str], reviewer: str) -> int:
        if decision not in {"APPROVE", "REJECT", "EDIT"}:
            raise ValueError("invalid review decision")
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        result = self.connection.execute(
            "INSERT INTO reviews (alert_id, decision, revised_summary, reviewer, created_at) VALUES (?, ?, ?, ?, ?)",
            (alert_id, decision, revised_summary or None, reviewer.strip(), utc_now().isoformat()),
        )
        self.connection.commit()
        return int(result.lastrowid)

    def dashboard_topics(self):
        return self.connection.execute(
            """SELECT t.topic_id, t.title, t.primary_category, t.secondary_categories, t.updated_at,
                w.post_count, w.capped_contributions, w.author_count, w.family_count, w.hhi, w.baseline,
                w.score, w.stage, w.components, a.id AS alert_id, a.recommendation, a.analysis_mode, a.payload,
                a.created_at AS alert_created_at
               FROM topics t
               JOIN topic_windows w ON w.topic_id=t.topic_id
               JOIN (SELECT topic_id, MAX(window_end) AS newest FROM topic_windows GROUP BY topic_id) newest
                    ON newest.topic_id=w.topic_id AND newest.newest=w.window_end
               LEFT JOIN alerts a ON a.id=(SELECT id FROM alerts ax WHERE ax.topic_id=t.topic_id ORDER BY ax.id DESC LIMIT 1)
               ORDER BY COALESCE(w.score, -1) DESC, t.title"""
        ).fetchall()

    def reviews_for_alert(self, alert_id: int):
        return self.connection.execute(
            "SELECT decision, revised_summary, reviewer, created_at FROM reviews WHERE alert_id=? ORDER BY id DESC", (alert_id,)
        ).fetchall()

    def latest_collection_run(self):
        return self.connection.execute(
            "SELECT source_key, finished_at, status, pages, inserted_posts, coverage, error FROM collection_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
