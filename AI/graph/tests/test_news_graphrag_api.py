"""Retrieval date constraints, citation rejection and strict news API boundary."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from news_graphrag import retrieve, validate_generation
from serve_news_impact import create_app


class RagTests(unittest.TestCase):
    def test_future_evidence_never_retrieved(self):
        article = dict(record_id="q", published_date="2024-01-01", title="query", body="", companies=[])
        edge = {"type": "PARTNER", "evidence": [dict(id="past", date="2023-12-31", text="known"),
                                                  dict(id="future", date="2024-01-02", text="unknown")]}
        route = {"path": [edge], "source": "A"}
        archive = [dict(record_id="future_news", published_date="2025-01-01", title="later", body="",
                        companies=[{"ticker": "A"}, {"ticker": "B"}])]
        evidence = retrieve(article, "B", route, archive)
        self.assertEqual({e["record_id"] for e in evidence}, {"q", "past"})

    def test_generator_cannot_change_score_or_cite_missing_source(self):
        evidence = [{"id": "E1", "text": "삼성전자와 한국전력이 협약을 체결했다."}]
        for raw in ['{"claims":[{"evidence_id":"E9","quote":"삼성전자와 한국전력이 협약을 체결했다."}]}',
                    '{"claims":[{"evidence_id":"E1","quote":"삼성전자와 한국전력이 협약을 체결했다."}],"impact_score":1}',
                    '{"claims":[{"evidence_id":"E1","quote":"2024년에 두 기업이 인수 합병했다."}]}']:
            self.assertIsNone(validate_generation(raw, evidence)[0])
        text, ids = validate_generation('{"claims":[{"evidence_id":"E1","quote":"삼성전자와 한국전력이 협약을 체결했다."}]}', evidence)
        self.assertIn("삼성전자와 한국전력이 협약을 체결했다.", text)
        self.assertEqual(ids, ["E1"])


class StubPredictor:
    metadata = {"model_version": "test"}
    tickers = ["005930"]

    def predict(self, **kwargs):
        return {"status": "OK", "model_ver": kwargs["model_ver"]}


class APITests(unittest.TestCase):
    def test_strict_news_contract(self):
        good = dict(news_id="a", title="삼성전자", published_at="2025-01-01", region="domestic")
        with patch("serve_news_impact.NewsImpactPredictor", return_value=StubPredictor()):
            with TestClient(create_app()) as c:
                self.assertEqual(c.post("/v1/impact/predict", json=good).json()["model_ver"], "stage3")
                for bad in [{**good, "horizon": 2}, {**good, "horizon": True}, {**good, "top_k": "3"},
                            {**good, "impact_score": 1}, {**good, "title": " "},
                            {**good, "published_at": "2025-01-01T12:00:00"}]:
                    self.assertEqual(c.post("/v1/impact/predict", json=bad).status_code, 422, bad)
                self.assertEqual(c.post("/v1/impact/explain", json=good).status_code, 422)
                self.assertEqual(c.post("/v1/impact/explain", json={**good, "target_ticker": "005930"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
