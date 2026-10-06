"""Windowed, explainable trend score as specified in the project plan."""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from app.categories import classify, load_taxonomy
from app.clustering import match_event, normalize_for_family
from app.repository import Repository


WINDOW = timedelta(minutes=15)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _window_metrics(rows: Iterable[object]) -> dict:
    rows = list(rows)
    N = len(rows)
    if not N:
        return {"N": 0, "n": 0, "U": 0, "F": 0, "HHI": None}
    families = [hashlib.sha256(normalize_for_family(row["content"]).encode()).hexdigest() for row in rows]
    capped = {(row["author_id"], family) for row, family in zip(rows, families)}
    author_counts = Counter(row["author_id"] for row in rows)
    hhi = sum((count / N) ** 2 for count in author_counts.values())
    return {"N": N, "n": len(capped), "U": len(author_counts), "F": len(set(families)), "HHI": hhi}


def score_metrics(current: dict, historical: List[dict]) -> dict:
    if len(historical) != 3:
        return {**current, "baseline": None, "score": None, "stage": "insufficient_history", "components": {}}
    baseline = sum(item["n"] for item in historical) / 3
    if current["N"] == 0:
        return {**current, "baseline": baseline, "score": None, "stage": "stable", "components": {}}
    growth = max(0.0, min(1.0, math.log2((current["n"] + 1) / (baseline + 1)) / 3))
    volume = min(1.0, current["n"] / 30)
    authors = min(1.0, current["U"] / 20)
    duplication = 1 - current["F"] / current["N"]
    support = min(1.0, current["U"] / 5)
    score = 100 * (0.50 * growth + 0.20 * volume + 0.30 * authors)
    score *= (1 - 0.60 * duplication) * (1 - 0.50 * current["HHI"]) * support
    if baseline == 0 and support > 0:
        stage = "emerging"
    elif current["n"] >= 1.5 * baseline and current["n"] - baseline >= 5:
        stage = "growing"
    elif current["n"] <= 0.67 * baseline and baseline - current["n"] >= 5:
        stage = "declining"
    else:
        stage = "stable"
    return {**current, "baseline": baseline, "score": round(score, 2), "stage": stage,
            "components": {"G": round(growth, 4), "V": round(volume, 4), "A": round(authors, 4),
                           "D": round(duplication, 4), "support": round(support, 4)}}


def analyze(repository: Repository, as_of: datetime, taxonomy_path: str = "config/themes.json") -> List[dict]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must include timezone")
    taxonomy = load_taxonomy(taxonomy_path)
    starts = [as_of - WINDOW * offset for offset in range(4, -1, -1)]
    events: Dict[str, dict] = {}
    for index in range(4):
        rows = repository.posts_between(_iso(starts[index]), _iso(starts[index + 1]))
        grouped = defaultdict(list)
        for row in rows:
            event = match_event(row["content"])
            if event:
                grouped[event.topic_id].append(row)
                events.setdefault(event.topic_id, {"event": event, "rows": defaultdict(list)})["rows"][index].append(row)
        # Topic absent from one observed window remains a legitimate zero.
    results = []
    for topic_id, bundle in events.items():
        event = bundle["event"]
        grouped_rows = bundle["rows"]
        current = _window_metrics(grouped_rows[3])
        historical = [_window_metrics(grouped_rows[index]) for index in range(3)]
        metrics = score_metrics(current, historical)
        all_rows = [row for window_rows in grouped_rows.values() for row in window_rows]
        primary, secondary, category_reason = classify(event.title, taxonomy)
        repository.upsert_topic(topic_id, event.title, primary, secondary, event.reason + "; " + category_reason,
                                taxonomy["version"], all_rows[0]["timestamp"], all_rows[-1]["timestamp"])
        repository.save_topic_window(topic_id, _iso(starts[3]), _iso(starts[4]), metrics)
        results.append({"topic_id": topic_id, "topic": event.title, "primary_category": primary,
                        "secondary_categories": secondary, **metrics})
    return sorted(results, key=lambda item: (item["score"] is not None, item["score"] or -1), reverse=True)
