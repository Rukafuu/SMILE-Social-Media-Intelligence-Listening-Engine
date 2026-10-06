"""Generate a deterministic, explicitly synthetic paginated feed."""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


EVENTS = [
    ("Bitcoin permanece estável após sessão de ETFs fictícios", "crypto_web3"),
    ("Token Aurora fictício cresce após anúncio de protocolo", "crypto_web3"),
    ("Regulador fictício aprova consulta sobre ETF cripto", "politics_regulation"),
    ("Exchange Aurora anuncia listagem fictícia", "business_finance"),
    ("Clube Horizonte fecha patrocínio cripto fictício", "sports_entertainment"),
    ("Mercado debate juros e ETF cripto fictício", "investments_markets"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--as-of", default="2026-10-06T15:00:00Z")
    parser.add_argument("--output", default="data/synthetic_feed.jsonl")
    parser.add_argument("--count", type=int, default=480)
    args = parser.parse_args()
    randomizer = random.Random(args.seed)
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")).astimezone(timezone.utc)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for index in range(args.count):
        event, category = EVENTS[index % len(EVENTS)]
        timestamp = as_of - timedelta(minutes=60) + timedelta(seconds=index * 7)
        # Aurora's volume rises only in the final 15-minute publishing window.
        # Earlier windows include a small, observed baseline for the same topic.
        if timestamp >= as_of - timedelta(minutes=15) and index % 5 != 0:
            event, category = EVENTS[1]
        records.append({
            "post_id": "synthetic-%04d" % index, "platform": "synthetic", "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "content": "%s — perspectiva sintética %d" % (event, index), "author_id": "synthetic:author-%02d" % (index % 45),
            "source_url": "local://synthetic-%04d" % index, "likes": randomizer.randint(0, 100),
            "comments": randomizer.randint(0, 20), "reposts": randomizer.randint(0, 15),
            "views": None if index % 9 == 0 else randomizer.randint(50, 500), "is_synthetic": True,
            "category_hint": category,
        })
    with output.open("w", encoding="utf-8") as destination:
        for record in records:
            destination.write(json.dumps(record, ensure_ascii=False) + "\n")
    print("generated %d synthetic records at %s" % (len(records), output))


if __name__ == "__main__":
    main()
