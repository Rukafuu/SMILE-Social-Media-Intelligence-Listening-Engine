"""Local JSONL connector with real cursor-based page requests."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models import Post
from app.repository import Repository


class TransientCollectionError(RuntimeError):
    pass


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
        return Page(items, None if exhausted else str(end), exhausted,
                    {"kind": "synthetic_local_jsonl", "total_available": len(records), "query": query})


def collect_all(repository: Repository, feed: LocalJsonlFeed, source_key: str, query: str = "all", max_retries: int = 3) -> Dict[str, Any]:
    checkpoint = repository.get_checkpoint(source_key)
    if checkpoint and checkpoint["exhausted"]:
        return {"status": "already_exhausted", "pages": 0, "inserted": 0, "post_count": repository.post_count()}
    cursor = checkpoint["cursor"] if checkpoint else None
    seen_cursors = set()
    run_id = repository.start_run(source_key)
    pages = inserted = 0
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
            if page.exhausted:
                break
            cursor = page.next_cursor
        repository.finish_run(run_id, "success", pages, inserted)
        return {"status": "success", "pages": pages, "inserted": inserted, "post_count": repository.post_count()}
    except Exception as error:
        repository.finish_run(run_id, "failed", pages, inserted, str(error))
        raise
