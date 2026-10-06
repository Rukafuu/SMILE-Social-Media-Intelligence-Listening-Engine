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
        self.connection.execute("PRAGMA journal_mode = WAL")

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
            CREATE TABLE IF NOT EXISTS topic_classifications (
                id INTEGER PRIMARY KEY,
                topic_id TEXT NOT NULL REFERENCES topics(topic_id),
                primary_category TEXT,
                secondary_categories TEXT NOT NULL,
                classification_method TEXT NOT NULL,
                classification_reason TEXT NOT NULL,
                taxonomy_version TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        # Compatible migration for SQLite databases created by earlier MVP steps.
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(collection_runs)")}
        if "coverage" not in columns:
            self.connection.execute("ALTER TABLE collection_runs ADD COLUMN coverage TEXT")
        migrations = {
            "posts": {"source_kind": "TEXT NOT NULL DEFAULT 'unverified'"},
            "topic_windows": {"metrics_version": "INTEGER", "coverage": "TEXT", "circulation": "TEXT"},
            "alerts": {"window_start": "TEXT", "window_end": "TEXT", "metrics_version": "INTEGER", "analysis_key": "TEXT"},
            "checkpoints": {"prefix_hash": "TEXT"},
        }
        for table, fields in migrations.items():
            present = {row[1] for row in self.connection.execute("PRAGMA table_info(" + table + ")")}
            for name, definition in fields.items():
                if name not in present:
                    self.connection.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + definition)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS topic_snapshots (
                topic_id TEXT NOT NULL REFERENCES topics(topic_id), window_start TEXT NOT NULL,
                window_end TEXT NOT NULL, version INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                metrics TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(topic_id, window_start, window_end, version)
            );
            CREATE TABLE IF NOT EXISTS agent_calls (
                id INTEGER PRIMARY KEY, topic_id TEXT NOT NULL, window_end TEXT,
                metrics_version INTEGER, tool_name TEXT NOT NULL, arguments TEXT NOT NULL,
                evidence_refs TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS alert_analysis_key ON alerts(analysis_key) WHERE analysis_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS posts_timestamp ON posts(timestamp);
        """)
        self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            if not self.connection.in_transaction:
                self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def get_checkpoint(self, source_key: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT source_key, cursor, exhausted, updated_at, prefix_hash FROM checkpoints WHERE source_key = ?", (source_key,)
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

    def persist_page(self, source_key: str, next_cursor: Optional[str], exhausted: bool, posts: Iterable[Post], prefix_hash: Optional[str] = None) -> int:
        inserted = 0
        with self.transaction() as connection:
            for post in posts:
                if post.canonical_uri and connection.execute("SELECT 1 FROM posts WHERE canonical_uri=? LIMIT 1", (post.canonical_uri,)).fetchone():
                    continue
                normalized = " ".join(post.content.casefold().split())
                content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                result = connection.execute(
                    """INSERT OR IGNORE INTO posts (
                        platform, post_id, timestamp, collected_at, content, normalized_content, content_hash,
                        author_id, source_url, likes, comments, reposts, views, canonical_uri, repost_of,
                        cited_source, event_published_at, is_synthetic, source_kind
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (post.platform, post.post_id, post.timestamp.isoformat(), post.collected_at.isoformat(),
                     post.content, normalized, content_hash, post.author_id, post.source_url, post.likes,
                     post.comments, post.reposts, post.views, post.canonical_uri, post.repost_of,
                     post.cited_source, post.event_published_at.isoformat() if post.event_published_at else None,
                     int(post.is_synthetic), post.source_kind),
                )
                inserted += result.rowcount
            connection.execute(
                """INSERT INTO checkpoints (source_key, cursor, exhausted, updated_at, prefix_hash) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source_key) DO UPDATE SET cursor=excluded.cursor, exhausted=excluded.exhausted,
                   updated_at=excluded.updated_at, prefix_hash=excluded.prefix_hash""",
                (source_key, next_cursor, int(exhausted), utc_now().isoformat(), prefix_hash),
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

    def data_profile(self):
        """Describe the observed data origin without inferring social coverage."""
        return self.connection.execute(
            """SELECT COUNT(*) AS total_posts, COALESCE(SUM(is_synthetic), 0) AS synthetic_posts,
               MIN(timestamp) AS earliest_published_at, MAX(timestamp) AS latest_published_at FROM posts"""
        ).fetchone()

    def posts_between(self, start: str, end: str):
        return self.connection.execute(
            "SELECT * FROM posts WHERE julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?) ORDER BY julianday(timestamp), platform, post_id", (start, end)
        ).fetchall()

    def upsert_topic(self, topic_id: str, title: str, primary_category: Optional[str], secondary_categories: list,
                     reason: str, taxonomy_version: str, first_seen: str, last_seen: str) -> None:
        self.connection.execute(
            """INSERT INTO topics (topic_id, title, primary_category, secondary_categories, classification_method,
               classification_reason, taxonomy_version, first_seen_at, last_seen_at, updated_at)
               VALUES (?, ?, ?, ?, 'deterministic_rules', ?, ?, ?, ?, ?)
               ON CONFLICT(topic_id) DO UPDATE SET title=excluded.title,
               primary_category=CASE WHEN topics.classification_method='human_override' THEN topics.primary_category ELSE excluded.primary_category END,
               secondary_categories=CASE WHEN topics.classification_method='human_override' THEN topics.secondary_categories ELSE excluded.secondary_categories END,
               classification_method=CASE WHEN topics.classification_method='human_override' THEN topics.classification_method ELSE excluded.classification_method END,
               classification_reason=CASE WHEN topics.classification_method='human_override' THEN topics.classification_reason ELSE excluded.classification_reason END,
               taxonomy_version=CASE WHEN topics.classification_method='human_override' THEN topics.taxonomy_version ELSE excluded.taxonomy_version END,
               first_seen_at=MIN(topics.first_seen_at, excluded.first_seen_at), last_seen_at=MAX(topics.last_seen_at, excluded.last_seen_at), updated_at=excluded.updated_at""",
            (topic_id, title, primary_category, json.dumps(secondary_categories), reason, taxonomy_version,
             first_seen, last_seen, utc_now().isoformat()),
        )
        self.connection.commit()

    def override_topic_classification(self, topic_id: str, primary_category: Optional[str], secondary_categories: list,
                                      reviewer: str, taxonomy_version: str) -> None:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        if (secondary_categories and not primary_category) or len(secondary_categories) > 1 or primary_category in secondary_categories:
            raise ValueError("classification can have one primary and at most one distinct secondary category")
        reason = "manual classification by " + reviewer.strip()
        now = utc_now().isoformat()
        with self.transaction() as connection:
            connection.execute(
                """UPDATE topics SET primary_category=?, secondary_categories=?, classification_method='human_override',
                   classification_reason=?, taxonomy_version=?, updated_at=? WHERE topic_id=?""",
                (primary_category, json.dumps(secondary_categories), reason, taxonomy_version, now, topic_id),
            )
            connection.execute(
                """INSERT INTO topic_classifications (topic_id, primary_category, secondary_categories, classification_method,
                   classification_reason, taxonomy_version, reviewer, created_at) VALUES (?, ?, ?, 'human_override', ?, ?, ?, ?)""",
                (topic_id, primary_category, json.dumps(secondary_categories), reason, taxonomy_version, reviewer.strip(), now),
            )

    def save_topic_window(self, topic_id: str, window_start: str, window_end: str, metrics: dict, commit: bool = True) -> None:
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
        if commit:
            self.connection.commit()

    def latest_topic_metrics(self, topic_id: str):
        return self.connection.execute(
            "SELECT * FROM topic_windows WHERE topic_id=? ORDER BY window_end DESC LIMIT 1", (topic_id,)
        ).fetchone()

    def save_alert(self, topic_id: str, analysis_mode: str, recommendation: str, payload: dict) -> int:
        window = payload.get("analysis_window") or {}
        version = payload.get("metrics_version")
        key = payload.get("analysis_key")
        now = payload.setdefault("created_at", utc_now().isoformat())
        payload.setdefault("review_status", "pending")
        result = self.connection.execute(
            """INSERT OR IGNORE INTO alerts (topic_id, analysis_mode, recommendation, payload, created_at,
               window_start, window_end, metrics_version, analysis_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (topic_id, analysis_mode, recommendation, json.dumps(payload, ensure_ascii=False), now,
             window.get("start"), window.get("end"), version, key),
        )
        self.connection.commit()
        if key:
            return int(self.connection.execute("SELECT id FROM alerts WHERE analysis_key=?", (key,)).fetchone()[0])
        return int(result.lastrowid)

    def cached_alert(self, key: str):
        row = self.connection.execute("SELECT payload FROM alerts WHERE analysis_key=? OR analysis_key LIKE ? ORDER BY id DESC LIMIT 1", (key, key + ":retry:%")).fetchone()
        return json.loads(row[0]) if row else None

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
                w.score, w.stage, w.components, w.window_start, w.window_end, w.metrics_version, w.coverage, w.circulation, a.id AS alert_id, a.recommendation, a.analysis_mode, a.payload,
                a.created_at AS alert_created_at
               FROM topics t
               JOIN topic_windows w ON w.topic_id=t.topic_id
               JOIN (SELECT topic_id, MAX(window_end) AS newest FROM topic_windows GROUP BY topic_id) newest
                    ON newest.topic_id=w.topic_id AND newest.newest=w.window_end
               LEFT JOIN alerts a ON a.id=(SELECT id FROM alerts ax WHERE ax.topic_id=t.topic_id AND ax.window_start=w.window_start AND ax.window_end=w.window_end AND ax.metrics_version=w.metrics_version ORDER BY ax.id DESC LIMIT 1)
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


    def coverage_between(self, start: str, end: str) -> dict:
        from app.models import parse_utc
        runs = self.connection.execute("SELECT source_key, coverage FROM collection_runs WHERE status='success' ORDER BY id DESC").fetchall()
        intervals = []
        for row in runs:
            coverage = json.loads(row["coverage"] or "{}")
            if coverage.get("complete") and coverage.get("kind") == "synthetic_local_jsonl":
                intervals.append((parse_utc(coverage["complete_start"]), parse_utc(coverage["complete_end"]), row["source_key"]))
        cursor, stop = parse_utc(start), parse_utc(end)
        sources = set()
        for left, right, source in sorted(intervals):
            if left <= cursor < right:
                cursor = max(cursor, right)
                sources.add(source)
        return {"start": start, "end": end, "complete": cursor >= stop,
                "source_keys": sorted(sources), "basis": "verified_synthetic_fixture" if cursor >= stop else "unknown_or_partial"}

    def save_snapshot(self, topic_id, start, end, metrics, evidence):
        serialized = json.dumps({"metrics": metrics, "evidence": evidence}, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        with self.transaction() as connection:
            latest = connection.execute("SELECT version, fingerprint FROM topic_snapshots WHERE topic_id=? AND window_start=? AND window_end=? ORDER BY version DESC LIMIT 1", (topic_id, start, end)).fetchone()
            version = latest["version"] if latest and latest["fingerprint"] == fingerprint else (latest["version"] + 1 if latest else 1)
            self.save_topic_window(topic_id, start, end, metrics, commit=False)
            connection.execute("INSERT OR IGNORE INTO topic_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (topic_id, start, end, version, fingerprint, json.dumps(metrics), json.dumps(evidence, ensure_ascii=False), utc_now().isoformat()))
            connection.execute("UPDATE topic_windows SET metrics_version=?, coverage=?, circulation=? WHERE topic_id=? AND window_start=? AND window_end=?",
                               (version, json.dumps(metrics["coverage"]), json.dumps(metrics["circulation"]), topic_id, start, end))
        return version

    def snapshot(self, topic_id, start, end, version):
        return self.connection.execute("SELECT * FROM topic_snapshots WHERE topic_id=? AND window_start=? AND window_end=? AND version=?", (topic_id, start, end, version)).fetchone()

    def log_agent_call(self, topic, name, arguments, refs):
        window = topic.get("analysis_window") or {}
        self.connection.execute("INSERT INTO agent_calls (topic_id, window_end, metrics_version, tool_name, arguments, evidence_refs, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (topic["topic_id"], window.get("end"), topic.get("metrics_version"), name, json.dumps(arguments), json.dumps(refs), utc_now().isoformat()))
        self.connection.commit()
