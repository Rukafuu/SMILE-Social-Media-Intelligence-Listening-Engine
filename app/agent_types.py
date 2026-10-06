"""Read-only evidence projection shared by the agent and deterministic critic."""
from __future__ import annotations

from typing import List

from app.clustering import match_event


def evidence_for_topic(repository, topic_id: str, limit: int) -> List[dict]:
    result = []
    rows = repository.connection.execute("SELECT post_id, platform, timestamp, content, author_id, source_url, cited_source, event_published_at, is_synthetic FROM posts ORDER BY timestamp DESC").fetchall()
    for row in rows:
        event = match_event(row["content"])
        if event and event.topic_id == topic_id:
            result.append({key: row[key] for key in row.keys()})
        if len(result) >= limit:
            break
    return result
