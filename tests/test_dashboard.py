"""Exercise the actual Streamlit admin UI without browser/network access."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None

from app.agent import analyze_topic
from app.collectors import LocalJsonlFeed, collect_all
from app.repository import Repository
from app.trends import analyze
from scripts.generate_dataset import write_fixture

AS_OF = datetime(2026, 10, 6, 15, tzinfo=timezone.utc)


@unittest.skipUnless(AppTest, "install requirements.txt for dashboard integration tests")
class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.database = root / "ui.sqlite3"
        feed = root / "feed.jsonl"
        write_fixture(feed, AS_OF)
        repo = Repository(str(self.database)); repo.initialize()
        try:
            collect_all(repo, LocalJsonlFeed(str(feed)), "ui")
            for topic in analyze(repo, AS_OF):
                alert = analyze_topic(repo, topic, allow_llm=False)
                repo.save_alert(topic["topic_id"], alert["analysis_mode"], alert["recommendation"], alert)
        finally:
            repo.close()
        self.env = patch.dict(os.environ, {"SMILE_DATABASE":str(self.database)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py"), default_timeout=10).run()
        self.assertEqual(0, len(self.app.exception))

    def test_synthetic_dashboard_prevents_external_collection(self):
        button = next(x for x in self.app.button if x.label == "Buscar agora")
        self.assertTrue(button.disabled)
        self.assertGreater(len(self.app.dataframe), 0)

    def test_filter_with_no_matching_topic_does_not_crash(self):
        self.app.sidebar.selectbox[0].select("business_finance").run()
        self.app.sidebar.selectbox[1].select("investments_markets").run()
        self.assertEqual(0, len(self.app.exception))
        self.assertTrue(any("Nenhum tópico" in item.value for item in self.app.info))

    def test_approve_edit_reject_are_saved_separately_from_model(self):
        app = self.app
        next(x for x in app.text_input if x.label == "Revisor").set_value("ana")
        for decision in ("APPROVE", "EDIT", "REJECT"):
            next(x for x in app.selectbox if x.label == "Decisão").select(decision)
            next(x for x in app.text_area if x.label.startswith("Resumo revisado")).set_value("Resumo humano " + decision)
            next(x for x in app.button if x.label == "Registrar revisão").click().run()
            self.assertEqual(0, len(app.exception))
        repo = Repository(str(self.database))
        try:
            reviews = repo.connection.execute("SELECT decision FROM reviews ORDER BY id").fetchall()
            self.assertEqual(["APPROVE", "EDIT", "REJECT"], [row[0] for row in reviews])
            original = json.loads(repo.connection.execute("SELECT payload FROM alerts WHERE topic_id='token-aurora-protocol'").fetchone()[0])
            self.assertNotIn("Resumo humano", original["summary"])
        finally:
            repo.close()

    def test_manual_category_change_survives_reanalysis(self):
        app = self.app
        next(x for x in app.text_input if x.label == "Revisor da classificação").set_value("ana")
        next(x for x in app.selectbox if x.label == "Categoria principal").select("business_finance")
        next(x for x in app.button if x.label == "Salvar correção").click().run()
        self.assertEqual(0, len(app.exception))
        repo = Repository(str(self.database)); repo.initialize()
        try:
            topic = next(x for x in analyze(repo, AS_OF) if x["topic_id"] == "token-aurora-protocol")
            self.assertEqual("business_finance", topic["primary_category"])
            self.assertEqual(2, topic["metrics_version"])
            # An alert on version 1 is never displayed next to version 2 metrics.
            row = next(x for x in repo.dashboard_topics() if x["topic_id"] == topic["topic_id"])
            self.assertIsNone(row["alert_id"])
        finally:
            repo.close()


if __name__ == "__main__":
    unittest.main()
