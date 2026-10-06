"""Command line entry points for reproducible ingestion."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from app.settings import load_local_env
load_local_env()

from app.collectors import (LocalJsonlFeed, MastodonHashtagFeed, MastodonHashtagStream, SourceAccessError,
                            TransientCollectionError, build_mastodon_auth_url, collect_all, collect_stream,
                            exchange_mastodon_authorization_code)
from app.settings import upsert_local_env_value
from app.repository import Repository
from app.trends import analyze
from app.agent import AgentError, analyze_topic

def main() -> None:
    parser = argparse.ArgumentParser(prog="smile")
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
    collect.add_argument("--public", action="store_true", help="omit the token for a public Mastodon timeline")
    analyze_command = subparsers.add_parser("analyze", help="associate events and calculate trend scores")
    analyze_command.add_argument("--database", default="data/cryptobr.sqlite3")
    analyze_command.add_argument("--as-of", required=True)
    analyze_command.add_argument("--with-agent", action="store_true", help="create bounded analysis suggestions")
    analyze_command.add_argument("--max-agent-topics", type=int, default=5, help="live LLM budget; other topics use deterministic analysis")
    analyze_command.add_argument("--retry-agent", action="store_true", help="retry failed cached LLM attempts without replacing prior reviews")
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
    stream.add_argument("--public", action="store_true", help="connect without a token to a public hashtag stream")
    watch = subparsers.add_parser("watch", help="poll the local feed and refresh complete trend windows")
    watch.add_argument("--feed", default="data/synthetic_feed.jsonl")
    watch.add_argument("--database", default="data/cryptobr.sqlite3")
    watch.add_argument("--page-size", type=int, default=50)
    watch.add_argument("--source-key", default="local:synthetic:v1")
    watch.add_argument("--interval-seconds", type=float, default=60.0)
    watch.add_argument("--max-cycles", type=int, help="bounded run count for demos and tests")
    doctor = subparsers.add_parser("mastodon-doctor", help="validate Mastodon token without opening a stream")
    doctor.add_argument("--mastodon-base-url", default=os.getenv("MASTODON_BASE_URL"))
    auth_url = subparsers.add_parser("mastodon-auth-url", help="print the OAuth authorize URL for a real Mastodon login")
    auth_url.add_argument("--mastodon-base-url", default=os.getenv("MASTODON_BASE_URL"))
    auth_url.add_argument("--redirect-uri", default=os.getenv("MASTODON_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob"))
    authorize = subparsers.add_parser("mastodon-authorize", help="exchange one-time Mastodon authorization code for a user token")
    authorize.add_argument("--mastodon-base-url", default=os.getenv("MASTODON_BASE_URL"))
    authorize.add_argument("--redirect-uri", default=os.getenv("MASTODON_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob"))
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
                token = None if args.public else os.getenv("MASTODON_TOKEN")
                feed = MastodonHashtagFeed(args.mastodon_base_url, token, args.page_size)
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
                if args.max_agent_topics < 0:
                    parser.error("--max-agent-topics cannot be negative")
                # Ensure the injection scenario is actually included in the live budget.
                candidates = sorted(results, key=lambda item: ("prompt_injection_content" in item["circulation"]["risk_flags"], item["score"] or -1), reverse=True)
                allowed = {item["topic_id"] for item in candidates[:args.max_agent_topics]}
                for topic in results:
                    try:
                        alert = analyze_topic(repository, topic, retry=args.retry_agent, allow_llm=topic["topic_id"] in allowed)
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
            try:
                base_url = args.mastodon_base_url
                source_key = args.source_key or "mastodon-stream:%s:%s" % (base_url, args.hashtag.casefold())
                token = None if args.public else os.getenv("MASTODON_TOKEN")
                stream_source = MastodonHashtagStream(base_url, token)
                print(json.dumps(collect_stream(repository, stream_source, source_key, args.hashtag, args.max_events), ensure_ascii=False))
            except (SourceAccessError, TransientCollectionError, ValueError) as error:
                print(json.dumps({"status": "stream_unavailable", "reason": str(error)}, ensure_ascii=False))
            except KeyboardInterrupt:
                print(json.dumps({"status": "interrupted", "events": 0, "inserted": 0}, ensure_ascii=False))
        finally:
            repository.close()
    if args.command == "watch":
        if args.interval_seconds <= 0:
            parser.error("--interval-seconds must be greater than zero")
        if args.max_cycles is not None and args.max_cycles <= 0:
            parser.error("--max-cycles must be greater than zero")
        repository = Repository(args.database)
        repository.initialize()
        completed = 0
        try:
            while args.max_cycles is None or completed < args.max_cycles:
                result = collect_all(
                    repository,
                    LocalJsonlFeed(args.feed, args.page_size),
                    args.source_key,
                    reopen_exhausted=True,
                )
                # Only analyze closed fifteen-minute windows. The in-progress
                # interval is deliberately excluded from a trend conclusion.
                now = datetime.now(timezone.utc)
                closed = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
                topics = analyze(repository, closed)
                completed += 1
                print(json.dumps({"status": "watch_cycle_completed", "cycle": completed,
                                  "collection": result, "analysis_as_of": closed.isoformat(),
                                  "topics": len(topics)}, ensure_ascii=False))
                if args.max_cycles is None or completed < args.max_cycles:
                    time.sleep(args.interval_seconds)
        except KeyboardInterrupt:
            print(json.dumps({"status": "watch_interrupted", "cycles": completed}, ensure_ascii=False))
        finally:
            repository.close()
    if args.command == "mastodon-doctor":
        try:
            stream_source = MastodonHashtagStream(args.mastodon_base_url, os.getenv("MASTODON_TOKEN"))
            print(json.dumps({"status": "credentials_valid", **stream_source.verify_credentials()}, ensure_ascii=False))
        except (SourceAccessError, TransientCollectionError, ValueError) as error:
            print(json.dumps({"status": "credentials_invalid_or_unavailable", "reason": str(error)}, ensure_ascii=False))
    if args.command == "mastodon-auth-url":
        try:
            url = build_mastodon_auth_url(args.mastodon_base_url, os.getenv("MASTODON_CLIENT_ID"), args.redirect_uri)
            print(json.dumps({"status": "authorization_url", "url": url}, ensure_ascii=False))
        except ValueError as error:
            print(json.dumps({"status": "authorization_url_unavailable", "reason": str(error)}, ensure_ascii=False))
    if args.command == "mastodon-authorize":
        try:
            payload = exchange_mastodon_authorization_code(
                args.mastodon_base_url, os.getenv("MASTODON_CLIENT_ID"), os.getenv("MASTODON_CLIENT_SECRET"),
                os.getenv("MASTODON_AUTHORIZATION_CODE"), args.redirect_uri,
            )
            upsert_local_env_value("MASTODON_TOKEN", payload["access_token"],
                                   remove_keys=("MASTODON_AUTHORIZATION_CODE",))
            print(json.dumps({"status": "user_token_saved", "scope": payload.get("scope", "")}, ensure_ascii=False))
        except (SourceAccessError, TransientCollectionError, ValueError) as error:
            print(json.dumps({"status": "authorization_failed", "reason": str(error)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "events": 0, "inserted": 0}, ensure_ascii=False))

