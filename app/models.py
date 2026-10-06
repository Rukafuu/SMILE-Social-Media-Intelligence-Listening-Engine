"""Domain models shared by collectors and persistence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and normalize it to UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Post:
    post_id: str
    platform: str
    timestamp: datetime
    collected_at: datetime
    content: str
    author_id: str
    source_url: str
    likes: Optional[int] = None
    comments: Optional[int] = None
    reposts: Optional[int] = None
    views: Optional[int] = None
    canonical_uri: Optional[str] = None
    repost_of: Optional[str] = None
    cited_source: Optional[str] = None
    event_published_at: Optional[datetime] = None
    is_synthetic: bool = True
    source_kind: str = "unverified"

    @classmethod
    def from_feed(cls, payload: Dict[str, Any], collected_at: datetime) -> "Post":
        required = ("post_id", "platform", "timestamp", "content", "author_id")
        missing = [field for field in required if not payload.get(field)]
        if missing:
            raise ValueError("missing required fields: " + ", ".join(missing))
        for field in ("likes", "comments", "reposts", "views"):
            value = payload.get(field)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(field + " must be a nonnegative integer or null")
        synthetic = payload.get("is_synthetic", True)
        if type(synthetic) is not bool:
            raise ValueError("is_synthetic must be a boolean")
        kind = payload.get("source_kind", "unverified")
        if kind not in {"unverified", "synthetic_official"}:
            raise ValueError("unknown source_kind")
        if kind == "synthetic_official" and (not synthetic or payload["platform"] != "synthetic"):
            raise ValueError("simulation authority requires an explicitly synthetic post")
        return cls(
            post_id=str(payload["post_id"]),
            platform=str(payload["platform"]),
            timestamp=parse_utc(str(payload["timestamp"])),
            collected_at=collected_at.astimezone(timezone.utc),
            content=str(payload["content"]),
            author_id=str(payload["author_id"]),
            source_url=str(payload.get("source_url") or "local://" + str(payload["post_id"])),
            likes=payload.get("likes"), comments=payload.get("comments"),
            reposts=payload.get("reposts"), views=payload.get("views"),
            canonical_uri=payload.get("canonical_uri"), repost_of=payload.get("repost_of"),
            cited_source=payload.get("cited_source"),
            event_published_at=(parse_utc(payload["event_published_at"])
                                if payload.get("event_published_at") else None),
            is_synthetic=synthetic, source_kind=kind,
        )


