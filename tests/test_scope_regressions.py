"""Regression tests exercise the public pipeline, not only score helper stubs."""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.agent import analyze_topic
from app.agent_types import evidence_for_topic
from app.clustering import match_event
from app.collectors import LocalJsonlFeed, MastodonHashtagFeed, TransientCollectionError, collect_all
from app.models import Post
from app.repository import Repository
from app.trends import analyze
from scripts.generate_dataset import append_confirmation, build_records, write_fixture, write_manifest

AS_OF = datetime(2026, 10, 6, 15, tzinfo=timezone.utc)


def model_message(content=None, calls=None):
    return {"choices": [{"message": {"role": "assistant", "content": content,
                                       **({"tool_calls": calls} if calls is not None else {})}}]}


def tool(name, topic, limit=None):
    args = {"topic_id": topic["topic_id"]}
    if limit is not None:
        args["limit"] = limit
    return {"id": name, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ScopeRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.feed = self.root / "feed.jsonl"
        write_fixture(self.feed, AS_OF)
        self.repo = Repository(str(self.root / "db.sqlite"))
        self.repo.initialize()
        self.addCleanup(self.repo.close)
        self.env = patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "OPENROUTER_MODEL": "test-model"})
        self.env.start()
        self.addCleanup(self.env.stop)
        collect_all(self.repo, LocalJsonlFeed(str(self.feed)), "fixture")
        self.topics = analyze(self.repo, AS_OF)
        self.topic = next(x for x in self.topics if x["topic_id"] == "token-aurora-protocol")

    def evidence(self, topic=None):
        topic = topic or self.topic
        window = topic["analysis_window"]
        return evidence_for_topic(self.repo, topic["topic_id"], 8, window["start"], window["end"], topic["metrics_version"])

    def payload(self, topic=None):
        evidence = self.evidence(topic)
        item = evidence[0]
        return {"recommendation": "HIGHLIGHT", "summary": "Interpretação do sinal.",
                "claims": [{"text": item["content"][:280], "status": "observed", "evidence_refs": [item["ref"]]}],
                "evidence_refs": [item["ref"]], "uncertainties": [], "risk_flags": []}

    def run_model(self, payload=None, topic=None, limit=8, responses=None):
        topic = topic or self.topic
        if responses is None:
            responses = [model_message(calls=[tool("get_topic_metrics", topic), tool("get_topic_evidence", topic, limit)]),
                         model_message(json.dumps(payload or self.payload(topic)))]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "mock-only"}), patch("app.agent._request", side_effect=responses) as request:
            result = analyze_topic(self.repo, topic)
        return result, request

    def test_eight_scenarios_and_changed_ranking(self):
        by_id = {x["topic_id"]: x for x in self.topics}
        self.assertEqual(761, self.repo.post_count())
        self.assertEqual(10, len(by_id))
        self.assertEqual("stable", by_id["bitcoin-etf-stable"]["stage"])
        self.assertEqual("growing", by_id["token-aurora-protocol"]["stage"])
        self.assertLess(by_id["token-aurora-protocol"]["N"], by_id["bitcoin-etf-stable"]["N"])
        self.assertGreater(by_id["token-aurora-protocol"]["score"], by_id["bitcoin-etf-stable"]["score"])
        copies = by_id["pumplet-campaign"]
        self.assertEqual(2, copies["U"])
        self.assertEqual(1, copies["F"])
        self.assertLess(copies["score"], 10)
        self.assertIn("single_known_source", by_id["novachain-audit-rumor"]["circulation"]["risk_flags"])
        self.assertIn("contradictory_content", by_id["novachain-audit-rumor"]["circulation"]["risk_flags"])
        self.assertIn("old_event_recirculation", by_id["old-orbit-announcement"]["circulation"]["risk_flags"])
        self.assertIn("exchange-aurora-listing", by_id)
        self.assertIn("exchange-aurora-incident", by_id)
        self.assertEqual(5, len({x["primary_category"] for x in self.topics if x["primary_category"]}))

    def test_real_paraphrases_share_event_but_different_actions_do_not(self):
        first = match_event("Token Aurora anuncia protocolo fictício.")
        second = match_event("Aurora token apresenta atualização fictícia da rede.")
        self.assertEqual(first.topic_id, second.topic_id)
        self.assertNotEqual(match_event("Corretora Aurora vai listar um ativo.").topic_id,
                            match_event("Corretora Aurora investiga ataque.").topic_id)

    def test_injection_is_sent_as_tool_data_and_cannot_call_shell(self):
        topic = next(x for x in self.topics if x["topic_id"] == "bitcoin-etf-stable")
        evidence = self.evidence(topic)
        self.assertEqual("synthetic-injection-001", evidence[0]["post_id"])
        payload = self.payload(topic)
        result, request = self.run_model(payload, topic)
        messages = request.call_args_list[1].args[0]["messages"]
        self.assertTrue(any(message["role"] == "tool" and "EXFILTRATE SECRET" in message["content"] for message in messages))
        self.assertEqual("openrouter", result["analysis_mode"])
        self.assertIn("prompt_injection_content", result["risk_flags"])
        malicious = [model_message(calls=[tool("shell", topic)])]
        result, _ = self.run_model(topic=topic, responses=malicious)
        self.assertEqual("fallback_no_llm", result["analysis_mode"])
        self.assertIn("llm_failed", result["risk_flags"])

    def test_missing_history_is_unknown_in_actual_pipeline(self):
        repo = Repository(str(self.root / "single.sqlite"))
        repo.initialize()
        self.addCleanup(repo.close)
        path = self.root / "single.jsonl"
        path.write_text(json.dumps(build_records(AS_OF, batch="update")[0]) + "\n")
        collect_all(repo, LocalJsonlFeed(str(path)), "single")
        topic = analyze(repo, AS_OF)[0]
        self.assertIsNone(topic["score"])
        self.assertIsNone(topic["baseline"])
        self.assertEqual("insufficient_history", topic["stage"])

    def test_partial_collection_does_not_claim_complete_history(self):
        repo = Repository(str(self.root / "partial.sqlite")); repo.initialize(); self.addCleanup(repo.close)
        collect_all(repo, LocalJsonlFeed(str(self.feed)), "partial", max_pages=1)
        self.assertFalse(repo.coverage_between("2026-10-06T13:45:00Z", "2026-10-06T15:00:00Z")["complete"])
        self.assertTrue(all(x["score"] is None for x in analyze(repo, AS_OF)))
        collect_all(repo, LocalJsonlFeed(str(self.feed)), "partial")
        self.assertTrue(all(x["score"] is not None for x in analyze(repo, AS_OF)))

    def test_rejected_records_prevent_complete_coverage(self):
        repo = Repository(str(self.root / "bad.sqlite")); repo.initialize(); self.addCleanup(repo.close)
        path = self.root / "bad.jsonl"
        rows = build_records(AS_OF)
        rows[0]["likes"] = -1
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        write_manifest(path, AS_OF - timedelta(minutes=75), AS_OF)
        run = collect_all(repo, LocalJsonlFeed(str(path)), "bad")
        self.assertFalse(run["coverage"]["complete"])
        self.assertEqual(1, run["coverage"]["rejected_records"])

    def test_modified_fixture_prefix_cannot_resume_silently(self):
        with self.feed.open("r+") as handle:
            content = handle.read().replace("Opinião sintética", "Opinião alterada", 1)
            handle.seek(0); handle.write(content); handle.truncate()
        with self.assertRaisesRegex(ValueError, "prefix changed"):
            collect_all(self.repo, LocalJsonlFeed(str(self.feed)), "fixture", reopen_exhausted=True)

    def test_no_tools_response_cannot_be_live_analysis(self):
        payload = self.payload()
        result, request = self.run_model(responses=[model_message(json.dumps(payload))] * 2)
        self.assertEqual("fallback_no_llm", result["analysis_mode"])
        self.assertEqual(2, request.call_count)
        self.assertNotEqual("HIGHLIGHT", result["recommendation"])

    def test_invented_refs_are_not_replaced_with_real_refs(self):
        payload = self.payload()
        payload["evidence_refs"] = ["synthetic:invented"]
        payload["claims"][0]["evidence_refs"] = ["synthetic:invented"]
        result, _ = self.run_model(payload)
        self.assertEqual([], result["evidence_refs"])
        self.assertIn("invalid_evidence_reference", result["risk_flags"])
        self.assertEqual([], result["claims"][0]["evidence_refs"])
        self.assertEqual("MONITOR", result["recommendation"])

    def test_existing_but_unconsulted_refs_are_rejected(self):
        payload = self.payload()
        payload["evidence_refs"] = [self.evidence()[1]["ref"]]
        payload["claims"] = []
        result, _ = self.run_model(payload, limit=1)
        self.assertEqual([], result["evidence_refs"])
        self.assertIn("invalid_evidence_reference", result["risk_flags"])

    def test_fabricated_claim_cannot_become_primary_summary(self):
        payload = self.payload()
        payload["summary"] = "Lucro oficialmente confirmado de 999%."
        payload["claims"][0]["text"] = payload["summary"]
        payload["claims"][0]["status"] = "confirmed_in_simulation"
        result, _ = self.run_model(payload)
        self.assertEqual("MONITOR", result["recommendation"])
        self.assertEqual("unconfirmed", result["claims"][0]["status"])
        self.assertNotIn("999%", result["summary"])
        self.assertEqual("unverified_interpretation", result["model_summary_status"])

    def test_simulated_authority_is_labelled_and_verbatim(self):
        payload = self.payload()
        self.assertEqual("synthetic_official", self.evidence()[0]["source_kind"])
        payload["claims"][0]["status"] = "confirmed_in_simulation"
        result, _ = self.run_model(payload)
        self.assertEqual("confirmed_in_simulation", result["claims"][0]["status"])
        self.assertEqual("HIGHLIGHT", result["recommendation"])

    def test_missing_or_wrong_schema_falls_back_after_one_repair(self):
        topic = self.topic
        responses = [model_message(calls=[tool("get_topic_metrics",topic),tool("get_topic_evidence",topic)]),
                     model_message('{"recommendation":"HIGHLIGHT"}'),model_message('{"recommendation":[]}')]
        result, request = self.run_model(responses=responses)
        self.assertEqual("fallback_no_llm", result["analysis_mode"])
        self.assertEqual(3, request.call_count)

    def test_tool_and_model_budgets_are_enforced(self):
        result, request = self.run_model(responses=[model_message(calls=[tool("get_topic_metrics",self.topic)])] * 4)
        self.assertEqual(4, request.call_count)
        self.assertEqual("fallback_no_llm", result["analysis_mode"])
        result, _ = self.run_model(responses=[model_message(calls=[tool("get_topic_metrics", self.topic)] * 7)])
        self.assertEqual("fallback_no_llm", result["analysis_mode"])

    def test_http_failure_does_not_claim_live_llm(self):
        from app.agent import AgentError
        result, _ = self.run_model(responses=AgentError("provider failed"))
        self.assertEqual("fallback_no_llm", result["analysis_mode"])
        self.assertIn("llm_failed", result["risk_flags"])

    def test_evidence_is_immutable_and_cannot_leak_future_posts(self):
        frozen = self.evidence()
        post = Post.from_feed({"post_id":"future", "platform":"synthetic", "timestamp":"2026-10-06T15:05:00Z",
            "content":"Token Aurora protocolo futuro.", "author_id":"synthetic:future"}, AS_OF)
        self.repo.persist_page("future", None, True, [post])
        analyze(self.repo, AS_OF + timedelta(minutes=15))
        self.assertEqual(frozen, self.evidence())
        self.assertNotIn("future", [x["post_id"] for x in self.evidence()])

    def test_same_window_version_change_preserves_review_and_old_evidence(self):
        topic = next(x for x in self.topics if x["topic_id"] == "novachain-audit-rumor")
        old_evidence = self.evidence(topic)
        alert = analyze_topic(self.repo, topic)
        old_id = self.repo.save_alert(topic["topic_id"], alert["analysis_mode"], alert["recommendation"], alert)
        self.repo.save_review(old_id, "APPROVE", None, "ana")
        self.assertEqual(old_id, self.repo.save_alert(topic["topic_id"], alert["analysis_mode"], alert["recommendation"], analyze_topic(self.repo, topic)))
        append_confirmation(self.feed, AS_OF)
        collect_all(self.repo, LocalJsonlFeed(str(self.feed)), "fixture", reopen_exhausted=True)
        updated = next(x for x in analyze(self.repo, AS_OF) if x["topic_id"] == topic["topic_id"])
        self.assertEqual(2, updated["metrics_version"])
        self.assertEqual(old_evidence, self.evidence(topic))
        new_alert = analyze_topic(self.repo, updated)
        new_id = self.repo.save_alert(updated["topic_id"], new_alert["analysis_mode"], new_alert["recommendation"], new_alert)
        self.assertNotEqual(old_id, new_id)
        self.assertEqual("pending", new_alert["review_status"])
        self.assertFalse(self.repo.reviews_for_alert(new_id))
        self.assertEqual("APPROVE", self.repo.reviews_for_alert(old_id)[0]["decision"])
        self.assertTrue(any(c["status"] == "confirmed_in_simulation" for c in new_alert["claims"]))

    def test_unchanged_analysis_keeps_snapshot_and_cached_alert(self):
        payload = analyze_topic(self.repo, self.topic)
        self.repo.save_alert(self.topic["topic_id"], payload["analysis_mode"], payload["recommendation"], payload)
        again = next(x for x in analyze(self.repo, AS_OF) if x["topic_id"] == self.topic["topic_id"])
        self.assertEqual(1, again["metrics_version"])
        with patch("app.agent.dispatch_tool") as dispatch:
            self.assertEqual(payload, analyze_topic(self.repo, again))
        dispatch.assert_not_called()

    def test_old_alert_not_joined_to_new_metrics_in_dashboard(self):
        payload = analyze_topic(self.repo, self.topic)
        self.repo.save_alert(self.topic["topic_id"], payload["analysis_mode"], payload["recommendation"], payload)
        analyze(self.repo, AS_OF + timedelta(minutes=15))
        row = next(x for x in self.repo.dashboard_topics() if x["topic_id"] == self.topic["topic_id"])
        self.assertIsNone(row["alert_id"])

    def test_fractional_timestamps_use_temporal_half_open_window(self):
        rows = self.repo.posts_between("2026-10-06T14:45:00+00:00", "2026-10-06T15:00:00+00:00")
        self.assertEqual(345, len(rows))
        self.assertTrue(all(datetime.fromisoformat(row["timestamp"]) < AS_OF for row in rows))

    def test_missing_engagement_stays_unknown(self):
        self.assertIsNone(self.topic["engagement"]["views"]["total_observed"])
        self.assertEqual(0, self.topic["engagement"]["views"]["available_posts"])
        self.assertIsNone(self.topic["circulation"]["repost_total_proportion"])

    def test_synthetic_authority_cannot_be_attached_to_live_post(self):
        with self.assertRaises(ValueError):
            Post.from_feed({"post_id":"x", "platform":"mastodon", "timestamp":"2026-10-06T14:50:00Z",
                           "content":"x", "author_id":"x", "is_synthetic":False, "source_kind":"synthetic_official"}, AS_OF)

    def test_empty_covered_baseline_is_distinct_from_missing_history(self):
        topic = next(x for x in self.topics if x["topic_id"] == "regulator-crypto-etf")
        self.assertEqual(0, topic["baseline"])
        self.assertEqual("emerging", topic["stage"])
        self.assertTrue(all(window["complete"] for window in topic["coverage"]))

    def test_mixed_sources_cannot_produce_a_global_score(self):
        post = Post.from_feed({"post_id":"live", "platform":"mastodon", "timestamp":"2026-10-06T14:55:00Z",
                              "content":"Bitcoin ETFs", "author_id":"live", "is_synthetic":False}, AS_OF)
        self.repo.persist_page("live", None, True, [post])
        with self.assertRaisesRegex(ValueError, "separate databases"):
            analyze(self.repo, AS_OF)

    def test_canonical_uri_deduplicates_across_instance_ids(self):
        for identifier in ("one:42", "two:21"):
            post = Post.from_feed({"post_id":identifier, "platform":"mastodon", "timestamp":"2026-10-06T14:55:00Z",
                                  "content":"Bitcoin ETFs", "author_id":identifier, "is_synthetic":False,
                                  "canonical_uri":"https://origin.invalid/status/42"}, AS_OF)
            self.repo.persist_page(identifier, None, True, [post])
        self.assertEqual(762, self.repo.post_count())

    def test_legacy_database_migration_is_additive_and_repeatable(self):
        import sqlite3
        database = self.root / "legacy.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, topic_id TEXT NOT NULL, analysis_mode TEXT NOT NULL, recommendation TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
        connection.execute("INSERT INTO alerts VALUES (1, 'legacy', 'simulated', 'MONITOR', '{}', '2026-10-06T14:00:00Z')")
        connection.commit(); connection.close()
        repo = Repository(str(database)); self.addCleanup(repo.close)
        repo.initialize(); repo.initialize()
        row = repo.connection.execute("SELECT * FROM alerts WHERE id=1").fetchone()
        self.assertEqual("legacy", row["topic_id"])
        self.assertIsNone(row["metrics_version"])
        self.assertIsNone(row["analysis_key"])
        repo.upsert_topic("legacy", "Legacy", None, [], "legacy", "v1", "a", "b")
        repo.save_review(1, "APPROVE", None, "ana")
        self.assertEqual("APPROVE", repo.reviews_for_alert(1)[0]["decision"])

    def test_failed_llm_retry_creates_new_alert_without_replacing_review(self):
        from app.agent import AgentError
        payload, _ = self.run_model(responses=AgentError("failed"))
        old_id = self.repo.save_alert(self.topic["topic_id"], payload["analysis_mode"], payload["recommendation"], payload)
        self.repo.save_review(old_id, "APPROVE", None, "ana")
        responses = [model_message(calls=[tool("get_topic_metrics",self.topic),tool("get_topic_evidence",self.topic)]), model_message(json.dumps(self.payload()))]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY":"mock-only"}), patch("app.agent._request",side_effect=responses):
            retried = analyze_topic(self.repo, self.topic, retry=True)
        new_id = self.repo.save_alert(self.topic["topic_id"], retried["analysis_mode"], retried["recommendation"], retried)
        self.assertNotEqual(old_id, new_id)
        self.assertEqual("openrouter", retried["analysis_mode"])
        self.assertEqual("APPROVE", self.repo.reviews_for_alert(old_id)[0]["decision"])
        self.assertFalse(self.repo.reviews_for_alert(new_id))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY":"mock-only"}), patch("app.agent._request") as request:
            self.assertEqual(retried, analyze_topic(self.repo, self.topic))
        request.assert_not_called()

    def test_long_retry_after_stops_instead_of_retrying_early(self):
        feed = MastodonHashtagFeed("https://example.invalid")
        with patch.object(feed, "fetch_page", side_effect=TransientCollectionError("429", retry_after=90)) as fetch, patch("app.collectors.time.sleep") as sleep:
            with self.assertRaises(TransientCollectionError):
                collect_all(self.repo, feed, "rate-limited", query="bitcoin")
        self.assertEqual(1, fetch.call_count)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
