"""Local JSONL connector with real cursor-based page requests."""
from __future__ import annotations

import json
import hashlib
from email.utils import parsedate_to_datetime
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
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class SourceAccessError(RuntimeError):
    """The source rejected access; retrying cannot repair credentials or policy."""
    pass


def build_mastodon_auth_url(base_url: str, client_id: str, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob") -> str:
    """Build the exact OAuth authorize URL for a user code flow on this instance."""
    if not all((base_url, client_id, redirect_uri)):
        raise ValueError("base URL, client ID and redirect URI are required")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "read:statuses",
        "force_login": "true",
    }
    return base_url.rstrip("/") + "/oauth/authorize?" + urllib.parse.urlencode(params)


def exchange_mastodon_authorization_code(base_url: str, client_id: str, client_secret: str,
                                         code: str, redirect_uri: str) -> Dict[str, Any]:
    """Exchange a one-use authorization code for a user access token."""
    if not all((base_url, client_id, client_secret, code, redirect_uri)):
        raise ValueError("client ID, client secret, authorization code and redirect URI are required")
    encoded = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": client_id,
                                      "client_secret": client_secret, "code": code,
                                      "redirect_uri": redirect_uri}).encode("utf-8")
    request = urllib.request.Request(base_url.rstrip("/") + "/oauth/token", data=encoded, method="POST",
                                     headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (400, 401):
            raise SourceAccessError("authorization code exchange rejected; codes are one-use and redirect URI must match") from error
        raise TransientCollectionError("Mastodon authorization exchange HTTP error %s" % error.code) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise TransientCollectionError("Mastodon authorization exchange transport error") from error
    if not payload.get("access_token"):
        raise SourceAccessError("authorization exchange returned no access token")
    scopes = set(str(payload.get("scope", "")).split())
    if "read" not in scopes and "read:statuses" not in scopes:
        raise SourceAccessError("new user token does not include read:statuses")
    return payload


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
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self._failed_once = False

    def prefix_hash(self, cursor):
        digest = hashlib.sha256()
        count = 0
        with self.path.open("rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                if count >= int(cursor or 0):
                    break
                digest.update(line)
                count += 1
        if count != int(cursor or 0):
            raise ValueError("local feed shrank since checkpoint")
        return digest.hexdigest()

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
        coverage = {"kind": "synthetic_local_jsonl", "total_available": len(records), "query": query,
                    "earliest_published_at": min(timestamps) if timestamps else None,
                    "latest_published_at": max(timestamps) if timestamps else None, "complete": False}
        manifest_path = Path(str(self.path) + ".meta.json")
        if manifest_path.exists():
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if metadata.get("sha256") != hashlib.sha256(self.path.read_bytes()).hexdigest():
                raise ValueError("fixture hash differs from its coverage manifest")
            if metadata.get("kind") != "synthetic_fixture" or not all(row.get("is_synthetic") is True and row.get("platform") == "synthetic" for row in records):
                raise ValueError("complete coverage is allowed only for labelled synthetic fixtures")
            left = Post.from_feed({"post_id":"coverage", "platform":"synthetic", "timestamp":metadata["complete_start"], "content":"coverage", "author_id":"fixture"}, datetime.now(timezone.utc)).timestamp
            right = Post.from_feed({"post_id":"coverage", "platform":"synthetic", "timestamp":metadata["complete_end"], "content":"coverage", "author_id":"fixture"}, datetime.now(timezone.utc)).timestamp
            if left >= right:
                raise ValueError("invalid coverage interval")
            coverage.update({"complete_start": metadata["complete_start"], "complete_end": metadata["complete_end"], "complete": exhausted})
        return Page(items, str(end), exhausted, coverage)


class MastodonHashtagFeed:
    """Permitted API connector for a single Mastodon instance and hashtag.

    It intentionally makes no claim of global social coverage. The collector
    uses API-provided cursors and never scrapes HTML or bypasses access gates.
    """
    def __init__(self, base_url: str, token: Optional[str] = None, limit: int = 40) -> None:
        if not base_url:
            raise ValueError("MASTODON_BASE_URL is required for the Mastodon connector")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Mastodon instance must be an HTTPS URL without credentials")
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
                retry_after = None
                raw = error.headers.get("Retry-After") if error.headers else None
                if raw:
                    try:
                        retry_after = max(0, float(raw))
                    except ValueError:
                        try:
                            retry_after = max(0, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            retry_after = 30
                raise TransientCollectionError("Mastodon transient HTTP error %s" % error.code, retry_after) from error
            raise RuntimeError("Mastodon HTTP error %s" % error.code) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise TransientCollectionError("Mastodon transport error") from error
        next_cursor = self._next_cursor(link_header)
        items = [self._normalize(status, self.base_url) for status in statuses]
        exhausted = not bool(next_cursor)
        timestamps = [item["timestamp"] for item in items]
        return Page(items, next_cursor, exhausted, {
            "kind": "mastodon_hashtag", "instance": self.base_url, "hashtag": hashtag, "complete": False,
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
    def _normalize(status: Dict[str, Any], instance: Optional[str] = None) -> Dict[str, Any]:
        account = status.get("account") or {}
        namespace = urllib.parse.urlparse(instance).netloc.casefold() + ":" if instance else ""
        return {
            "post_id": namespace + str(status["id"]), "platform": "mastodon",
            "timestamp": status["created_at"], "content": strip_html(status.get("content", "")),
            "author_id": "mastodon:%s%s" % (namespace, account.get("id", "unknown")),
            "source_url": status.get("url") or status.get("uri") or "mastodon://" + str(status["id"]),
            "canonical_uri": status.get("uri"), "repost_of": str(status["reblog"]["id"]) if status.get("reblog") else None,
            "likes": status.get("favourites_count"), "comments": status.get("replies_count"),
            "reposts": status.get("reblogs_count"), "views": None, "is_synthetic": False,
        }


class MastodonHashtagStream:
    """Bounded SSE stream of public hashtag events, with optional credentials."""
    def __init__(self, base_url: str, token: Optional[str] = None) -> None:
        if not base_url:
            raise ValueError("MASTODON_BASE_URL is required for streaming")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Mastodon instance must be an HTTPS URL without credentials")
        self.base_url = base_url.rstrip("/")
        self.token = token

    def events(self, hashtag: str):
        tag = hashtag.lstrip("#").strip()
        url = "%s/api/v1/streaming/hashtag?%s" % (self._streaming_base_url(), urllib.parse.urlencode({"tag": tag}))
        headers = {"Accept": "text/event-stream"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                for event in parse_sse(response):
                    if event["event"] == "update":
                        yield MastodonHashtagFeed._normalize(json.loads(event["data"]), self.base_url)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                mode = "credential" if self.token else "public stream"
                raise SourceAccessError("Mastodon stream access denied (%s); %s is not accepted by this instance" % (error.code, mode)) from error
            if error.code == 429 or 500 <= error.code <= 599:
                raise TransientCollectionError("Mastodon stream transient HTTP error %s" % error.code) from error
            raise RuntimeError("Mastodon stream HTTP error %s" % error.code) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise TransientCollectionError("Mastodon stream transport error") from error

    def verify_credentials(self) -> Dict[str, str]:
        """Validate a user token without exposing it or opening a stream."""
        if not self.token:
            raise ValueError("MASTODON_TOKEN is required to validate credentials")
        request = urllib.request.Request(
            self.base_url + "/api/v1/accounts/verify_credentials",
            headers={"Accept": "application/json", "Authorization": "Bearer " + self.token},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                account = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise SourceAccessError("credential rejected; use a user access token from this instance with read:statuses") from error
            raise TransientCollectionError("Mastodon credential check HTTP error %s" % error.code) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise TransientCollectionError("Mastodon credential check transport error") from error
        return {"instance": self.base_url, "account_id": str(account.get("id", "unknown")), "valid": "true"}

    def _streaming_base_url(self) -> str:
        """Resolve the streaming hostname before attaching Authorization.

        Instances may redirect their REST host to another streaming hostname.
        Following that redirect with urllib safely drops Authorization, producing
        a misleading 401. The instance configuration supplies the destination.
        """
        payload = None
        # `configuration.urls.streaming` is exposed by modern Mastodon in v2;
        # retain v1 only for older compatible implementations.
        for version in ("v2", "v1"):
            request = urllib.request.Request(self.base_url + "/api/%s/instance" % version, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    candidate = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code == 404 and version == "v2":
                    continue
                raise TransientCollectionError("could not discover Mastodon streaming host") from error
            except (urllib.error.URLError, TimeoutError) as error:
                raise TransientCollectionError("could not discover Mastodon streaming host") from error
            streaming = ((candidate.get("configuration") or {}).get("urls") or {}).get("streaming")
            if streaming:
                payload = streaming
                break
        if not payload:
            return self.base_url
        parsed = urllib.parse.urlparse(payload)
        if parsed.scheme == "wss":
            return "https://" + parsed.netloc + parsed.path.rstrip("/")
        if parsed.scheme == "ws":
            return "http://" + parsed.netloc + parsed.path.rstrip("/")
        return payload.rstrip("/")


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
    coverage = {"kind": "mastodon_hashtag_stream", "instance": stream.base_url, "hashtag": hashtag, "complete": False}
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
    if max_retries <= 0 or (max_pages is not None and max_pages <= 0):
        raise ValueError("retry/page limits must be positive")
    checkpoint = repository.get_checkpoint(source_key)
    if isinstance(feed, LocalJsonlFeed) and checkpoint and checkpoint["cursor"] and not checkpoint["prefix_hash"]:
        raise ValueError("legacy checkpoint has no verified feed prefix; use an isolated source/database")
    if isinstance(feed, LocalJsonlFeed) and checkpoint and checkpoint["prefix_hash"]:
        if feed.prefix_hash(checkpoint["cursor"]) != checkpoint["prefix_hash"]:
            raise ValueError("local feed prefix changed; use an isolated source/database")
    if checkpoint and checkpoint["exhausted"] and not reopen_exhausted:
        return {"status": "already_exhausted", "pages": 0, "inserted": 0, "post_count": repository.post_count()}
    cursor = checkpoint["cursor"] if checkpoint else None
    seen_cursors = set()
    run_id = repository.start_run(source_key)
    pages = inserted = retries = 0
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
                        retries += 1
                        delay = error.retry_after
                        if delay is not None and delay > 30:
                            raise TransientCollectionError("server requested a wait above the 30-second demo budget; resume later") from error
                        time.sleep(delay if delay is not None else min(0.5 * (2 ** attempt), 2))
            if page is None:
                raise last_error or RuntimeError("page fetch failed")
            valid_posts = []
            now = datetime.now(timezone.utc)
            for item in page.items:
                try:
                    valid_posts.append(Post.from_feed(item, now))
                except (TypeError, ValueError) as error:
                    repository.record_invalid(source_key, cursor, item, str(error))
            prefix = feed.prefix_hash(page.next_cursor) if isinstance(feed, LocalJsonlFeed) else None
            inserted += repository.persist_page(source_key, page.next_cursor, page.exhausted, valid_posts, prefix)
            if isinstance(feed, MastodonHashtagFeed) and not page.exhausted:
                time.sleep(1)
            pages += 1
            coverage = dict(page.coverage)
            rejected = repository.connection.execute("SELECT COUNT(*) FROM invalid_records WHERE source_key=?", (source_key,)).fetchone()[0]
            if rejected:
                coverage["complete"] = False
                coverage["rejected_records"] = rejected
            coverage["retry_count"] = retries
            if page.exhausted:
                break
            if max_pages is not None and pages >= max_pages:
                repository.finish_run(run_id, "partial_page_limit", pages, inserted,
                                      "page limit reached; resume from checkpoint", coverage)
                return {"status": "partial_page_limit", "pages": pages, "inserted": inserted,
                        "post_count": repository.post_count(), "coverage": coverage}
            cursor = page.next_cursor
        repository.finish_run(run_id, "success", pages, inserted, coverage=coverage)
        return {"status": "success", "pages": pages, "inserted": inserted, "post_count": repository.post_count(), "retries": retries, "coverage": coverage}
    except Exception as error:
        repository.finish_run(run_id, "failed", pages, inserted, str(error), coverage)
        raise

