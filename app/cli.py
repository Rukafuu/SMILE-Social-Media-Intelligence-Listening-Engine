"""Command line entry points for reproducible ingestion."""
from __future__ import annotations

import argparse
import json
from datetime import datetime

from app.collectors import LocalJsonlFeed, collect_all
from app.repository import Repository
from app.trends import analyze
from app.agent import AgentError, analyze_topic

def main() -> None:
    parser = argparse.ArgumentParser(prog="cryptobr")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="collect the paged local feed")
    collect.add_argument("--feed", default="data/synthetic_feed.jsonl")
    collect.add_argument("--database", default="data/cryptobr.sqlite3")
    collect.add_argument("--page-size", type=int, default=50)
    collect.add_argument("--simulate-transient-error", action="store_true")
    collect.add_argument("--source-key", default="local:synthetic:v1")
    analyze_command = subparsers.add_parser("analyze", help="associate events and calculate trend scores")
    analyze_command.add_argument("--database", default="data/cryptobr.sqlite3")
    analyze_command.add_argument("--as-of", required=True)
    analyze_command.add_argument("--with-agent", action="store_true", help="create bounded analysis suggestions")
    review = subparsers.add_parser("review", help="record a human decision for an alert")
    review.add_argument("--database", default="data/cryptobr.sqlite3")
    review.add_argument("--alert-id", type=int, required=True)
    review.add_argument("--decision", choices=["APPROVE", "REJECT", "EDIT"], required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--summary")
    args = parser.parse_args()
    if args.command == "collect":
        repository = Repository(args.database)
        repository.initialize()
        try:
            feed = LocalJsonlFeed(args.feed, args.page_size, 2 if args.simulate_transient_error else None)
            print(json.dumps(collect_all(repository, feed, args.source_key), ensure_ascii=False))
        finally:
            repository.close()
    if args.command == "analyze":
        repository = Repository(args.database)
        repository.initialize()
        try:
            moment = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
            results = analyze(repository, moment)
            if args.with_agent:
                for topic in results:
                    try:
                        alert = analyze_topic(repository, topic)
                    except AgentError as error:
                        alert = {"topic_id": topic["topic_id"], "analysis_mode": "unavailable", "error": str(error), "recommendation": "MONITOR"}
                    topic["agent"] = alert
                    repository.save_alert(topic["topic_id"], alert["analysis_mode"], alert["recommendation"], alert)
            print(json.dumps(results, ensure_ascii=False, indent=2))
        finally:
            repository.close()
    if args.command == "review":
        repository = Repository(args.database)
        repository.initialize()
        try:
            review_id = repository.save_review(args.alert_id, args.decision, args.summary, args.reviewer)
            print(json.dumps({"status": "saved", "review_id": review_id, "alert_id": args.alert_id}, ensure_ascii=False))
        finally:
            repository.close()


if __name__ == "__main__":
    main()
