"""Run the complete synthetic pipeline without credentials or network calls."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent import analyze_topic
from app.agent_types import evidence_for_topic
from app.collectors import LocalJsonlFeed, collect_all
from app.repository import Repository
from app.trends import analyze
from scripts.generate_dataset import append_confirmation, write_fixture

AS_OF = datetime(2026, 10, 6, 15, tzinfo=timezone.utc)


def run_demo(database, feed):
    repository = Repository(str(database)); repository.initialize()
    try:
        write_fixture(feed, AS_OF, batch="initial")
        initial = collect_all(repository, LocalJsonlFeed(str(feed), fail_on_page=2), "demo")
        reexecution = collect_all(repository, LocalJsonlFeed(str(feed)), "demo")
        write_fixture(feed, AS_OF, batch="update", append=True)
        update = collect_all(repository, LocalJsonlFeed(str(feed)), "demo", reopen_exhausted=True)
        topics = analyze(repository, AS_OF)
        volume = sorted(topics, key=lambda item: (-item["N"], item["topic_id"]))
        ranking = []
        alerts = []
        for index, topic in enumerate(topics, 1):
            alert = analyze_topic(repository, topic, allow_llm=False)
            alert_id = repository.save_alert(topic["topic_id"], alert["analysis_mode"], alert["recommendation"], alert)
            alerts.append({"alert_id": alert_id, **alert})
            ranking.append({"topic_id":topic["topic_id"], "posts":topic["N"], "authors":topic["U"],
                            "baseline":topic["baseline"], "score":topic["score"], "stage":topic["stage"],
                            "rank_score":index, "rank_volume":next(i + 1 for i, row in enumerate(volume) if row["topic_id"] == topic["topic_id"]),
                            "recommendation":alert["recommendation"], "risk_flags":alert["risk_flags"]})
        malicious = next(topic for topic in topics if topic["topic_id"] == "bitcoin-etf-stable")
        sent = evidence_for_topic(repository, malicious["topic_id"], 8)
        rumor = next(topic for topic in topics if topic["topic_id"] == "novachain-audit-rumor")
        old_alert = next(alert for alert in alerts if alert["topic_id"] == rumor["topic_id"])
        repository.save_review(old_alert["alert_id"], "APPROVE", None, "demo-reviewer")
        # Demonstrate all three actions on distinct alerts without publishing anything.
        edit_alert = next(alert for alert in alerts if alert["topic_id"] == "token-aurora-protocol")
        repository.save_review(edit_alert["alert_id"], "EDIT", "Resumo humano: acompanhar o sinal fictício.", "demo-reviewer")
        reject_alert = next(alert for alert in alerts if alert["topic_id"] == "pumplet-campaign")
        repository.save_review(reject_alert["alert_id"], "REJECT", None, "demo-reviewer")
        append_confirmation(feed, AS_OF)
        collect_all(repository, LocalJsonlFeed(str(feed)), "demo", reopen_exhausted=True)
        changed = next(topic for topic in analyze(repository, AS_OF) if topic["topic_id"] == rumor["topic_id"])
        new_alert = analyze_topic(repository, changed, allow_llm=False)
        new_id = repository.save_alert(changed["topic_id"], new_alert["analysis_mode"], new_alert["recommendation"], new_alert)
        result = {"dataset_label":"synthetic_only", "seed":42, "as_of":AS_OF.isoformat(),
                  "llm_execution":"none; deterministic tools only", "collection_initial":initial,
                  "collection_reexecution":reexecution, "collection_update":update,
                  "post_count_before_confirmation":761, "post_count_after_confirmation":repository.post_count(),
                  "ranking":ranking, "alerts":alerts,
                  "injection_evidence_refs":[item["ref"] for item in sent],
                  "review_evolution":{"topic_id":rumor["topic_id"], "old_alert_id":old_alert["alert_id"],
                      "old_version":rumor["metrics_version"], "old_reviews":[dict(row) for row in repository.reviews_for_alert(old_alert["alert_id"])],
                      "new_alert_id":new_id, "new_version":changed["metrics_version"],
                      "new_review_status":new_alert["review_status"], "new_reviews":[], "new_alert":new_alert}}
        assert result["post_count_after_confirmation"] == 762
        assert "synthetic:synthetic-injection-001" in result["injection_evidence_refs"]
        assert changed["metrics_version"] == 2 and old_alert["alert_id"] != new_id
        assert not repository.reviews_for_alert(new_id)
        return result
    finally:
        repository.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="examples/results.json")
    parser.add_argument("--database", help="optional fresh database to open in the admin dashboard")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        database = Path(args.database) if args.database else Path(directory) / "demo.sqlite3"
        if database.exists():
            parser.error("--database must be a new path; existing data/reviews are preserved")
        result = run_demo(database, Path(directory) / "feed.jsonl")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status":"demo_passed", "posts":result["post_count_before_confirmation"], "topics":len(result["ranking"]),
                      "retry_count":result["collection_initial"]["retries"], "output":str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
