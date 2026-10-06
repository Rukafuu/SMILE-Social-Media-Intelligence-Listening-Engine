"""Command line entry points for reproducible ingestion."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

from app.collectors import LocalJsonlFeed, MastodonHashtagFeed, MastodonHashtagStream, collect_all, collect_stream
from app.repository import Repository
from app.trends import analyze
from app.agent import AgentError, analyze_topic

def main() -> None:
    parser = argparse.ArgumentParser(prog="cryptobr")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="collect the paged local feed")
    collect.add_argument("--source", choices=["local", "mastodon"], default="local")
    collect.add_argument("--feed", default="data/synthetic_feed.jsonl")
    collect.add_argument("--database", default="data/cryptobr.sqlite3")
    collect.add_argument("--page-size", type=int, default=50)
    collect.add_argument("--simulate-transient-error", action="store_true")
    collect.add_argument("--source-key")
    collect.add_argument("--reopen-exhausted", action="store_true", help="poll an exhausted local feed for appended records")
    collect.add_argument("--hashtag", help="Mastodon hashtag (without #)")
    collect.add_argument("--mastodon-base-url", default=os.getenv("MASTODON_BASE_URL"))
    collect.add_argument("--max-pages", type=int, help="stop cleanly after this many pages (default: 10 for Mastodon)")
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
    stream = subparsers.add_parser("stream", help="collect bounded real-time Mastodon hashtag events")
    stream.add_argument("--database", default="data/mastodon_stream.sqlite3")
    stream.add_argument("--hashtag", required=True)
    stream.add_argument("--mastodon-base-url", default=os.getenv("MASTODON_BASE_URL"))
    stream.add_argument("--source-key")
    stream.add_argument("--max-events", type=int, default=20)
    args = parser.parse_args()
    if args.command == "collect":
        repository = Repository(args.database)
        repository.initialize()
        try:
            if args.source == "local":
                feed = LocalJsonlFeed(args.feed, args.page_size, 2 if args.simulate_transient_error else None)
                source_key = args.source_key or "local:synthetic:v1"
                query = "all"
            else:
                if not args.hashtag:
                    parser.error("--hashtag is required for --source mastodon")
                feed = MastodonHashtagFeed(args.mastodon_base_url, os.getenv("MASTODON_TOKEN"), args.page_size)
                source_key = args.source_key or "mastodon:%s:%s" % (args.mastodon_base_url, args.hashtag.casefold())
                query = args.hashtag
            max_pages = args.max_pages if args.max_pages is not None else (10 if args.source == "mastodon" else None)
            print(json.dumps(collect_all(repository, feed, source_key, query=query,
                                         reopen_exhausted=args.reopen_exhausted, max_pages=max_pages), ensure_ascii=False))
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
    if args.command == "stream":
        repository = Repository(args.database)
        repository.initialize()
        try:
            base_url = args.mastodon_base_url
            source_key = args.source_key or "mastodon-stream:%s:%s" % (base_url, args.hashtag.casefold())
            stream_source = MastodonHashtagStream(base_url, os.getenv("MASTODON_TOKEN"))
            print(json.dumps(collect_stream(repository, stream_source, source_key, args.hashtag, args.max_events), ensure_ascii=False))
        finally:
            repository.close()


if __name__ == "__main__":
    main()
