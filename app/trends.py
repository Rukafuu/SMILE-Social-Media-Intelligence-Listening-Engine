"""Windowed, explainable trend score as specified in the project plan."""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from app.categories import classify, load_taxonomy
from app.clustering import match_event, normalize_for_family, lexical
from app.models import parse_utc
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
    capped = {((row["platform"], row["author_id"]), family) for row, family in zip(rows, families)}
    author_counts = Counter((row["platform"], row["author_id"]) for row in rows)
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


def circulation_metrics(rows, as_of):
    rows = list(rows)
    origins = {row["cited_source"] for row in rows if row["cited_source"]}
    claim_origins = {row["cited_source"] for row in rows if row["cited_source"] and
                     not any(term in lexical(row["content"]) for term in ("nega ", "contraditor", "desment"))}
    linked = sum(bool(row["cited_source"]) for row in rows)
    reposts = sum(bool(row["repost_of"]) for row in rows)
    old = any(row["event_published_at"] and parse_utc(row["event_published_at"]) < as_of - WINDOW for row in rows)
    texts = [lexical(row["content"]) for row in rows]
    flags = []
    if rows and len({(row["platform"], row["author_id"]) for row in rows}) < 5:
        flags.append("low_author_support")
    families = {normalize_for_family(row["content"]) for row in rows}
    if rows and 1 - len(families) / len(rows) > 0.6:
        flags.append("high_repetition")
    if len(claim_origins) == 1:
        flags.append("single_known_source")
    if linked < len(rows):
        flags.append("incomplete_origin_links")
    if old:
        flags.append("old_event_recirculation")
    if any(any(word in text for word in ("nega ", "negou ", "contraditor", "desment")) for text in texts):
        flags.append("contradictory_content")
    if any(any(word in text for word in ("ignore instru", "ignore previous", "execute shell", "exfiltrate")) for text in texts):
        flags.append("prompt_injection_content")
    return {"known_origins": len(origins), "known_claim_origins": len(claim_origins), "origin_link_coverage": round(linked / len(rows), 4) if rows else None,
            "known_repost_minimum": round(reposts / len(rows), 4) if rows else None,
            "repost_total_proportion": None, "independence": "unknown", "region": "unknown",
            "risk_flags": flags}


def evidence_projection(row):
    fields = ("post_id", "platform", "timestamp", "content", "author_id", "source_url", "cited_source",
              "event_published_at", "is_synthetic", "source_kind")
    result = {key: row[key] for key in fields}
    result["ref"] = row["platform"] + ":" + row["post_id"]
    return result


def analyze(repository: Repository, as_of: datetime, taxonomy_path: str = "config/themes.json") -> List[dict]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must include timezone")
    as_of = as_of.astimezone(timezone.utc)
    as_of = as_of.replace(minute=as_of.minute // 15 * 15, second=0, microsecond=0)
    profile = repository.data_profile()
    if profile["synthetic_posts"] not in (0, profile["total_posts"]):
        raise ValueError("synthetic and external samples require separate databases")
    taxonomy = load_taxonomy(taxonomy_path)
    starts = [as_of - WINDOW * offset for offset in range(4, -1, -1)]
    coverage = [repository.coverage_between(_iso(starts[i]), _iso(starts[i + 1])) for i in range(4)]
    events = {}
    for index in range(4):
        for row in repository.posts_between(_iso(starts[index]), _iso(starts[index + 1])):
            event = match_event(row["content"])
            if event:
                events.setdefault(event.topic_id, {"event": event, "rows": defaultdict(list)})["rows"][index].append(row)
    results = []
    for topic_id, bundle in events.items():
        event, grouped = bundle["event"], bundle["rows"]
        current = _window_metrics(grouped[3])
        historical = [_window_metrics(grouped[index]) for index in range(3)]
        complete = all(item["complete"] for item in coverage)
        metrics = score_metrics(current, historical if complete else [])
        current_rows = grouped[3]
        circulation = circulation_metrics(current_rows, as_of)
        if current["HHI"] is not None and current["HHI"] > 0.25:
            circulation["risk_flags"].append("high_author_concentration")
        if not complete:
            circulation["risk_flags"].append("insufficient_coverage")
        engagement = {name: {"available_posts": sum(row[name] is not None for row in current_rows),
                             "total_observed": sum(row[name] for row in current_rows if row[name] is not None)
                             if any(row[name] is not None for row in current_rows) else None}
                      for name in ("likes", "comments", "reposts", "views")}
        all_rows = [row for rows in grouped.values() for row in rows]
        primary, secondary, reason = classify(event.title, taxonomy)
        repository.upsert_topic(topic_id, event.title, primary, secondary, event.reason + "; " + reason,
                                taxonomy["version"], all_rows[0]["timestamp"], all_rows[-1]["timestamp"])
        category = repository.connection.execute("SELECT primary_category, secondary_categories FROM topics WHERE topic_id=?", (topic_id,)).fetchone()
        import json
        metrics.update({"coverage": coverage, "circulation": circulation, "engagement": engagement,
                        "primary_category": category["primary_category"], "secondary_categories": json.loads(category["secondary_categories"])})
        start, end = _iso(starts[3]), _iso(starts[4])
        version = repository.save_snapshot(topic_id, start, end, metrics, [evidence_projection(row) for row in current_rows])
        results.append({"topic_id": topic_id, "topic": event.title, "analysis_window": {"start": start, "end": end},
                        "metrics_version": version, **metrics})
    return sorted(results, key=lambda item: (item["score"] is not None, item["score"] or -1, item["topic_id"]), reverse=True)
