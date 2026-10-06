"""Local JSONL connector with real cursor-based page requests."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models import Post
from app.repository import Repository


class TransientCollectionError(RuntimeError):
    pass


class SourceAccessError(RuntimeError):
    """The source rejected access; retrying cannot repair credentials or policy."""
    pass


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(content: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(content or "")
    return " ".join("".join(extractor.parts).split())


@dataclass
class Page:
    items: List[Dict[str, Any]]
    next_cursor: Optional[str]
    exhausted: bool
    coverage: Dict[str, Any]


class LocalJsonlFeed:
    def __init__(self, path: str, page_size: int = 50, fail_on_page: Optional[int] = None) -> None:
        self.path = Path(path)
        self.page_size = page_size
        self.fail_on_page = fail_on_page
        self._failed_once = False

    def fetch_page(self, query: str, cursor: Optional[str]) -> Page:
        start = int(cursor or 0)
        page_number = start // self.page_size + 1
        if self.fail_on_page == page_number and not self._failed_once:
            self._failed_once = True
            raise TransientCollectionError("controlled transient source failure")
        with self.path.open(encoding="utf-8") as source:
            records = [json.loads(line) for line in source if line.strip()]
        items = records[start:start + self.page_size]
        end = start + len(items)
        exhausted = end >= len(records)
        # Retain the numeric position even at exhaustion. If a later source poll
        # exposes newly appended records, the collector can continue safely.
        timestamps = [record.get("timestamp") for record in records if record.get("timestamp")]
        return Page(items, str(end), exhausted,
                    {"kind": "synthetic_local_jsonl", "total_available": len(records), "query": query,
                     "earliest_published_at": min(timestamps) if timestamps else None,
                     "latest_published_at": max(timestamps) if timestamps else None})


class MastodonHashtagFeed:
    """Permitted API connector for a single Mastodon instance and hashtag.

    It intentionally makes no claim of global social coverage. The collector
    uses API-provided cursors and never scrapes HTML or bypasses access gates.
    """
    def __init__(self, base_url: str, token: Optional[str] = None, limit: int = 40) -> None:
        if not base_url:
            raise ValueError("MASTODON_BASE_URL is required for the Mastodon connector")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.limit = min(max(limit, 1), 40)

    def fetch_page(self, query: str, cursor: Optional[str]) -> Page:
        hashtag = query.lstrip("#").strip()
        if not hashtag:
            raise ValueError("a Mastodon hashtag is required")
        params = {"limit": str(self.limit)}
        if cursor:
            params["max_id"] = cursor
        url = "%s/api/v1/timelines/tag/%s?%s" % (
            self.base_url, urllib.parse.quote(hashtag, safe=""), urllib.parse.urlencode(params)
        )
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                statuses = json.loads(response.read().decode("utf-8"))
                link_header = response.headers.get("Link", "")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise SourceAccessError("Mastodon access denied (%s); check instance policy or token" % error.code) from error
            if error.code == 429 or 500 <= error.code <= 599:
                raise TransientCollectionError("Mastodon transient HTTP error %s" % error.code) from error
            raise RuntimeError("Mastodon HTTP error %s" % error.code) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise TransientCollectionError("Mastodon transport error") from error
        next_cursor = self._next_cursor(link_header)
        items = [self._normalize(status) for status in statuses]
        exhausted = not bool(next_cursor)
        timestamps = [item["timestamp"] for item in items]
        return Page(items, next_cursor, exhausted, {
            "kind": "mastodon_hashtag", "instance": self.base_url, "hashtag": hashtag,
            "returned_items": len(items), "earliest_published_at": min(timestamps) if timestamps else None,
            "latest_published_at": max(timestamps) if timestamps else None,
        })

    @staticmethod
    def _next_cursor(link_header: str) -> Optional[str]:
        for section in link_header.split(","):
            if 'rel="next"' not in section:
                continue
            if "<" not in section or ">" not in section:
                continue
            url = section.split("<", 1)[1].split(">", 1)[0]
            value = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("max_id", [None])[0]
            if value:
                return value
        return None

    @staticmethod
    def _normalize(status: Dict[str, Any]) -> Dict[str, Any]:
        account = status.get("account") or {}
        return {
            "post_id": str(status["id"]), "platform": "mastodon",
            "timestamp": status["created_at"], "content": strip_html(status.get("content", "")),
            "author_id": "mastodon:%s" % account.get("id", "unknown"),
            "source_url": status.get("url") or status.get("uri") or "mastodon://" + str(status["id"]),
            "canonical_uri": status.get("uri"), "repost_of": str(status["reblog"]["id"]) if status.get("reblog") else None,
            "likes": status.get("favourites_count"), "comments": status.get("replies_count"),
            "reposts": status.get("reblogs_count"), "views": None, "is_synthetic": False,
        }


class MastodonHashtagStream:
    """Bounded SSE stream of new public statuses for one authorized hashtag."""
    def __init__(self, base_url: str, token: str) -> None:
        if not base_url or not token:
            raise ValueError("MASTODON_BASE_URL and MASTODON_TOKEN are required for streaming")
        self.base_url = base_url.rstrip("/")
        self.token = token

    def events(self, hashtag: str):
        tag = hashtag.lstrip("#").strip()
        url = "%s/api/v1/streaming/hashtag?%s" % (self.base_url, urllib.parse.urlencode({"tag": tag}))
        request = urllib.request.Request(url, headers={"Accept": "text/event-stream", "Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                for event in parse_sse(response):
                    if event["event"] == "update":
                        yield MastodonHashtagFeed._normalize(json.loads(event["data"]))
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise SourceAccessError("Mastodon stream access denied (%s); token requires read:statuses" % error.code) from error
            if error.code == 429 or 500 <= error.code <= 599:
                raise TransientCollectionError("Mastodon stream transient HTTP error %s" % error.code) from error
            raise RuntimeError("Mastodon stream HTTP error %s" % error.code) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise TransientCollectionError("Mastodon stream transport error") from error


def parse_sse(lines):
    """Parse minimal Server-Sent Events without trusting payload contents."""
    event = "message"
    data = []
    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        line = line.rstrip("\r\n")
        if not line:
            if data:
                yield {"event": event, "data": "\n".join(data)}
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def collect_stream(repository: Repository, stream: MastodonHashtagStream, source_key: str, hashtag: str,
                   max_events: int = 20, max_reconnects: int = 3) -> Dict[str, Any]:
    """Collect a bounded set of SSE updates while retaining idempotency per post."""
    run_id = repository.start_run(source_key)
    received = inserted = reconnects = 0
    coverage = {"kind": "mastodon_hashtag_stream", "instance": stream.base_url, "hashtag": hashtag}
    try:
        while received < max_events and reconnects <= max_reconnects:
            try:
                for payload in stream.events(hashtag):
                    now = datetime.now(timezone.utc)
                    post = Post.from_feed(payload, now)
                    inserted += repository.persist_page(source_key, post.post_id, False, [post])
                    received += 1
                    coverage["last_event_published_at"] = payload["timestamp"]
                    if received >= max_events:
                        break
                break
            except TransientCollectionError:
                reconnects += 1
                if reconnects > max_reconnects:
                    raise
                time.sleep(min(0.25 * (2 ** (reconnects - 1)), 2))
        repository.finish_run(run_id, "stream_completed", received, inserted, coverage=coverage)
        return {"status": "stream_completed", "events": received, "inserted": inserted,
                "post_count": repository.post_count(), "coverage": coverage}
    except Exception as error:
        repository.finish_run(run_id, "failed", received, inserted, str(error), coverage)
        raise


def collect_all(repository: Repository, feed: LocalJsonlFeed, source_key: str, query: str = "all", max_retries: int = 3,
                reopen_exhausted: bool = False, max_pages: Optional[int] = None) -> Dict[str, Any]:
    checkpoint = repository.get_checkpoint(source_key)
    if checkpoint and checkpoint["exhausted"] and not reopen_exhausted:
        return {"status": "already_exhausted", "pages": 0, "inserted": 0, "post_count": repository.post_count()}
    cursor = checkpoint["cursor"] if checkpoint else None
    seen_cursors = set()
    run_id = repository.start_run(source_key)
    pages = inserted = 0
    coverage = {}
    try:
        while True:
            if cursor in seen_cursors:
                raise RuntimeError("repeated cursor detected")
            seen_cursors.add(cursor)
            last_error = None
            page = None
            for attempt in range(max_retries):
                try:
                    page = feed.fetch_page(query, cursor)
                    break
                except TransientCollectionError as error:
                    last_error = error
                    if attempt + 1 < max_retries:
                        time.sleep(min(0.05 * (2 ** attempt), 0.2))
            if page is None:
                raise last_error or RuntimeError("page fetch failed")
            valid_posts = []
            now = datetime.now(timezone.utc)
            for item in page.items:
                try:
                    valid_posts.append(Post.from_feed(item, now))
                except (TypeError, ValueError) as error:
                    repository.record_invalid(source_key, cursor, item, str(error))
            inserted += repository.persist_page(source_key, page.next_cursor, page.exhausted, valid_posts)
            pages += 1
            coverage = page.coverage
            if page.exhausted:
                break
            if max_pages is not None and pages >= max_pages:
                repository.finish_run(run_id, "partial_page_limit", pages, inserted,
                                      "page limit reached; resume from checkpoint", coverage)
                return {"status": "partial_page_limit", "pages": pages, "inserted": inserted,
                        "post_count": repository.post_count(), "coverage": coverage}
            cursor = page.next_cursor
        repository.finish_run(run_id, "success", pages, inserted, coverage=coverage)
        return {"status": "success", "pages": pages, "inserted": inserted, "post_count": repository.post_count()}
    except Exception as error:
        repository.finish_run(run_id, "failed", pages, inserted, str(error), coverage)
        raise
