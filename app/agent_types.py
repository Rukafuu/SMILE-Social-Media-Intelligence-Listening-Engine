"""Immutable evidence of the exact topic/window/version under analysis."""
from __future__ import annotations

import json
from app.clustering import lexical


def evidence_priority(item):
    text = lexical(item["content"])
    if any(term in text for term in ("ignore instru", "ignore previous", "execute shell", "exfiltrate")):
        return 0
    if item.get("source_kind") == "synthetic_official":
        return 1
    if any(term in text for term in ("nega ", "contraditor", "desment")):
        return 2
    return 3


def evidence_for_topic(repository, topic_id, limit, window_start=None, window_end=None, version=None):
    if window_start is None:
        metrics = repository.latest_topic_metrics(topic_id)
        if not metrics or metrics["metrics_version"] is None:
            return []
        window_start, window_end, version = metrics["window_start"], metrics["window_end"], metrics["metrics_version"]
    snapshot = repository.snapshot(topic_id, window_start, window_end, version)
    if not snapshot:
        return []
    evidence = json.loads(snapshot["evidence"])
    # Risks and authority must reach the model; then prefer distinct wording.
    evidence.sort(key=lambda item: (evidence_priority(item), item["timestamp"], item["ref"]))
    distinct, repeated, seen = [], [], set()
    for item in evidence:
        normalized = lexical(item["content"])
        (repeated if normalized in seen else distinct).append(item)
        seen.add(normalized)
    return (distinct + repeated)[:limit]
